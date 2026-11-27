package org.sanatani.waapp;

import android.graphics.SurfaceTexture;
import android.media.AudioFormat;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaDataSource;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.opengl.Matrix;
import android.os.Handler;
import android.os.HandlerThread;
import android.view.Surface;

import org.json.JSONObject;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Hardware-accelerated Android MediaCodec audio (16kHz mono WAV) and video
 * (<=480p 15fps Baseline/Main H.264 Annex-B, 0 B-frames, 1s IDR) transcoder
 * for smooth WhatsApp voice & video call streaming.
 */
public final class WaMediaTranscoder {

    private WaMediaTranscoder() {}

    /**
     * Decode the first audio track of inputPath and write a clean 16kHz, 16-bit mono
     * PCM WAV file to outWavPath. Properly tracks INFO_OUTPUT_FORMAT_CHANGED so
     * HE-AAC SBR / Parametric Stereo sample-rate & channel updates never cause slow-mo.
     *
     * @return "" on success, or an error description string on failure.
     */
    public static String extractAudioToWav16k(String inputPath, String outWavPath) {
        MediaExtractor extractor = new MediaExtractor();
        BufferedOutputStream bos = null;
        File tmpFile = new File(outWavPath + ".tmp");

        try {
            extractor.setDataSource(inputPath);
            bos = new BufferedOutputStream(new FileOutputStream(tmpFile), 32768);
            bos.write(new byte[44]);

            long[] outBytes = new long[1];
            String err = decodeAudioTrackToPcm16k(extractor, bos, 600_000_000L, outBytes, false);
            if (!err.isEmpty()) {
                return err;
            }
            bos.flush();
            bos.close();
            bos = null;

            long totalOutBytes = outBytes[0];
            if (totalOutBytes == 0) {
                return "Decoded zero audio PCM bytes";
            }

            // Write true RIFF/WAVE header (16kHz, 1 channel, 16-bit)
            try (RandomAccessFile raf = new RandomAccessFile(tmpFile, "rw")) {
                raf.seek(0);
                byte[] hdr = new byte[44];
                ByteBuffer hb = ByteBuffer.wrap(hdr).order(ByteOrder.LITTLE_ENDIAN);
                hb.put("RIFF".getBytes("US-ASCII"));
                hb.putInt((int) (36 + totalOutBytes));
                hb.put("WAVE".getBytes("US-ASCII"));
                hb.put("fmt ".getBytes("US-ASCII"));
                hb.putInt(16);          // Subchunk1Size (PCM)
                hb.putShort((short) 1); // AudioFormat = 1 (PCM)
                hb.putShort((short) 1); // NumChannels = 1 (Mono)
                hb.putInt(16000);       // SampleRate = 16000
                hb.putInt(16000 * 2);   // ByteRate = SampleRate * 2
                hb.putShort((short) 2); // BlockAlign = 2
                hb.putShort((short) 16);// BitsPerSample = 16
                hb.put("data".getBytes("US-ASCII"));
                hb.putInt((int) totalOutBytes);
                raf.write(hdr);
            }

            File finalFile = new File(outWavPath);
            if (finalFile.exists()) {
                //noinspection ResultOfMethodCallIgnored
                finalFile.delete();
            }
            if (!tmpFile.renameTo(finalFile)) {
                return "Failed to move output WAV file";
            }
            return "";
        } catch (Throwable t) {
            return "Java audio transcode failed: " + t.getMessage();
        } finally {
            try { if (bos != null) bos.close(); } catch (Throwable ignored) {}
            try { extractor.release(); } catch (Throwable ignored) {}
            if (tmpFile.exists()) {
                //noinspection ResultOfMethodCallIgnored
                tmpFile.delete();
            }
        }
    }

    private static final ConcurrentHashMap<Integer, String> STREAM_STATES = new ConcurrentHashMap<>();

    public static String getStreamState(int pcmPort) {
        String st = STREAM_STATES.get(pcmPort);
        return st != null ? st : "";
    }

    public static void clearStreamState(int pcmPort) {
        STREAM_STATES.remove(pcmPort);
    }

    /**
     * Stream an HTTP/HTTPS audio URL (e.g. YouTube direct M4A/WebM stream) directly through
     * Android MediaExtractor + MediaCodec and emit live 16kHz mono s16le PCM to Go's loopback
     * TCP socket with zero full-file disk download.
     *
     * @return "" on normal EOF or caller-initiated socket stop; error description otherwise.
     */
    public static String streamUrlToPcmSocket(
            String streamUrl,
            String headersJson,
            long contentLength,
            String pcmHost,
            int pcmPort,
            int maxSeconds) {
        Map<String, String> headers = parseHeadersJson(headersJson);
        RangedHttpMediaDataSource dataSource = null;
        MediaExtractor extractor = new MediaExtractor();
        Socket socket = null;
        OutputStream pcmOut = null;
        STREAM_STATES.put(pcmPort, "connecting");

        try {
            dataSource = new RangedHttpMediaDataSource(streamUrl, headers, contentLength);
            extractor.setDataSource(dataSource);

            long maxPtsUs = (maxSeconds > 0) ? (maxSeconds * 1_000_000L) : Long.MAX_VALUE;
            socket = new Socket();
            socket.connect(new InetSocketAddress(pcmHost, pcmPort), 5000);
            socket.setTcpNoDelay(true);
            socket.setSoTimeout(0);
            final OutputStream rawOut = new BufferedOutputStream(socket.getOutputStream(), 4096);
            final boolean[] markedStreaming = new boolean[1];
            pcmOut = new OutputStream() {
                @Override
                public void write(int b) throws IOException {
                    rawOut.write(b);
                    if (!markedStreaming[0]) {
                        markedStreaming[0] = true;
                        STREAM_STATES.put(pcmPort, "streaming");
                    }
                }

                @Override
                public void write(byte[] b, int off, int len) throws IOException {
                    rawOut.write(b, off, len);
                    if (!markedStreaming[0] && len > 0) {
                        markedStreaming[0] = true;
                        STREAM_STATES.put(pcmPort, "streaming");
                    }
                }

                @Override
                public void flush() throws IOException {
                    rawOut.flush();
                }

                @Override
                public void close() throws IOException {
                    rawOut.close();
                }
            };

            long[] outBytes = new long[1];
            String err = decodeAudioTrackToPcm16k(extractor, pcmOut, maxPtsUs, outBytes, true);
            if (!err.isEmpty()) {
                STREAM_STATES.put(pcmPort, "error:" + err);
                return err;
            }
            pcmOut.flush();
            if (outBytes[0] <= 0) {
                STREAM_STATES.put(pcmPort, "error:Decoded zero audio PCM bytes");
                return "Decoded zero audio PCM bytes from stream";
            }
            STREAM_STATES.put(pcmPort, "done");
            return "";
        } catch (PcmSinkClosedException stopClosed) {
            // Peer (Go CallStopAudio / call ended) intentionally closed PCM socket -> normal stop
            STREAM_STATES.put(pcmPort, "stopped");
            return "";
        } catch (HttpRefusedException refused) {
            String err = "HTTP_REFUSED:" + refused.statusCode;
            STREAM_STATES.put(pcmPort, "error:" + err);
            return err;
        } catch (Throwable t) {
            if (dataSource != null && dataSource.refusedStatus > 0) {
                String err = "HTTP_REFUSED:" + dataSource.refusedStatus;
                STREAM_STATES.put(pcmPort, "error:" + err);
                return err;
            }
            String msg = t.getMessage();
            String err = "Stream decode failed: " + (msg != null ? msg : t.getClass().getSimpleName());
            STREAM_STATES.put(pcmPort, "error:" + err);
            return err;
        } finally {
            try { if (pcmOut != null) pcmOut.close(); } catch (Throwable ignored) {}
            try { if (socket != null) socket.close(); } catch (Throwable ignored) {}
            try { extractor.release(); } catch (Throwable ignored) {}
            try { if (dataSource != null) dataSource.close(); } catch (Throwable ignored) {}
        }
    }

    private static String decodeAudioTrackToPcm16k(
            MediaExtractor extractor,
            OutputStream pcmOut,
            long maxPtsUs,
            long[] totalOutBytesRef,
            boolean flushEachBuffer) throws IOException, PcmSinkClosedException {
        MediaCodec codec = null;
        try {
            int trackIdx = -1;
            MediaFormat inFmt = null;
            String mime = "";
            int count = extractor.getTrackCount();
            for (int i = 0; i < count; i++) {
                MediaFormat fmt = extractor.getTrackFormat(i);
                String m = fmt.getString(MediaFormat.KEY_MIME);
                if (m != null && m.startsWith("audio/")) {
                    trackIdx = i;
                    inFmt = fmt;
                    mime = m;
                    break;
                }
            }
            if (trackIdx < 0 || inFmt == null) {
                return "No audio track found in media";
            }

            extractor.selectTrack(trackIdx);
            int curRate = inFmt.containsKey(MediaFormat.KEY_SAMPLE_RATE)
                    ? inFmt.getInteger(MediaFormat.KEY_SAMPLE_RATE) : 44100;
            int curCh = inFmt.containsKey(MediaFormat.KEY_CHANNEL_COUNT)
                    ? inFmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT) : 2;
            int curPcmEnc = inFmt.containsKey(MediaFormat.KEY_PCM_ENCODING)
                    ? inFmt.getInteger(MediaFormat.KEY_PCM_ENCODING) : AudioFormat.ENCODING_PCM_16BIT;

            codec = MediaCodec.createDecoderByType(mime);
            codec.configure(inFmt, null, null, 0);
            codec.start();

            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean inEos = false;
            boolean outEos = false;
            long totalOutBytes = 0;

            final double targetRate = 16000.0;
            double pos = 0.0;
            float prevMono = 0.0f;
            boolean havePrev = false;
            byte[] rawScratch = new byte[65536];
            byte[] outChunk = new byte[16384];
            int outChunkPos = 0;

            while (!outEos) {
                if (!inEos) {
                    int inIdx = codec.dequeueInputBuffer(5000);
                    if (inIdx >= 0) {
                        ByteBuffer inBuf = codec.getInputBuffer(inIdx);
                        int sz = inBuf != null ? extractor.readSampleData(inBuf, 0) : -1;
                        long pts = extractor.getSampleTime();
                        if (sz < 0 || (pts >= 0 && pts > maxPtsUs)) {
                            codec.queueInputBuffer(inIdx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inEos = true;
                        } else {
                            codec.queueInputBuffer(inIdx, 0, sz, Math.max(0, pts), 0);
                            extractor.advance();
                        }
                    }
                }

                int outIdx = codec.dequeueOutputBuffer(info, 5000);
                if (outIdx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    MediaFormat outFmt = codec.getOutputFormat();
                    if (outFmt.containsKey(MediaFormat.KEY_SAMPLE_RATE)) {
                        int newRate = outFmt.getInteger(MediaFormat.KEY_SAMPLE_RATE);
                        if (newRate > 0) curRate = newRate;
                    }
                    if (outFmt.containsKey(MediaFormat.KEY_CHANNEL_COUNT)) {
                        int newCh = outFmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT);
                        if (newCh > 0) curCh = newCh;
                    }
                    if (outFmt.containsKey(MediaFormat.KEY_PCM_ENCODING)) {
                        curPcmEnc = outFmt.getInteger(MediaFormat.KEY_PCM_ENCODING);
                    }
                } else if (outIdx >= 0) {
                    ByteBuffer outBuf = codec.getOutputBuffer(outIdx);
                    if (outBuf != null && info.size > 0) {
                        outBuf.position(info.offset);
                        outBuf.limit(info.offset + info.size);
                        if (rawScratch.length < info.size) {
                            rawScratch = new byte[info.size];
                        }
                        outBuf.get(rawScratch, 0, info.size);

                        int bytesPerSample = (curPcmEnc == AudioFormat.ENCODING_PCM_FLOAT) ? 4 : 2;
                        int frameBytes = Math.max(1, curCh) * bytesPerSample;
                        int frames = info.size / frameBytes;

                        if (frames > 0) {
                            float[] mono = new float[frames];
                            ByteBuffer bb = ByteBuffer.wrap(rawScratch, 0, frames * frameBytes).order(ByteOrder.LITTLE_ENDIAN);
                            for (int f = 0; f < frames; f++) {
                                float acc = 0.0f;
                                for (int c = 0; c < curCh; c++) {
                                    if (bytesPerSample == 4) {
                                        acc += bb.getFloat();
                                    } else {
                                        acc += (bb.getShort() / 32768.0f);
                                    }
                                }
                                mono[f] = acc / curCh;
                            }

                            if (curRate == 16000) {
                                for (int f = 0; f < frames; f++) {
                                    int s16 = clampS16(mono[f]);
                                    if (outChunkPos + 2 > outChunk.length) {
                                        writePcmOrThrow(pcmOut, outChunk, outChunkPos, false);
                                        totalOutBytes += outChunkPos;
                                        outChunkPos = 0;
                                    }
                                    outChunk[outChunkPos++] = (byte) (s16 & 0xFF);
                                    outChunk[outChunkPos++] = (byte) ((s16 >> 8) & 0xFF);
                                }
                            } else {
                                double step = curRate / targetRate;
                                double base = havePrev ? 1.0 : 0.0;
                                int srcLen = havePrev ? (frames + 1) : frames;
                                while (true) {
                                    double idx = pos + base;
                                    int i0 = (int) idx;
                                    if (i0 + 1 >= srcLen) {
                                        break;
                                    }
                                    double frac = idx - i0;
                                    float s0 = (havePrev && i0 == 0) ? prevMono : mono[havePrev ? (i0 - 1) : i0];
                                    int i1 = i0 + 1;
                                    float s1 = (havePrev && i1 == 0) ? prevMono : mono[havePrev ? (i1 - 1) : i1];
                                    int s16 = clampS16((float) (s0 * (1.0 - frac) + s1 * frac));
                                    if (outChunkPos + 2 > outChunk.length) {
                                        writePcmOrThrow(pcmOut, outChunk, outChunkPos, false);
                                        totalOutBytes += outChunkPos;
                                        outChunkPos = 0;
                                    }
                                    outChunk[outChunkPos++] = (byte) (s16 & 0xFF);
                                    outChunk[outChunkPos++] = (byte) ((s16 >> 8) & 0xFF);
                                    pos += step;
                                }
                                pos -= frames;
                                prevMono = mono[frames - 1];
                                havePrev = true;
                            }

                            if (flushEachBuffer && outChunkPos > 0) {
                                writePcmOrThrow(pcmOut, outChunk, outChunkPos, true);
                                totalOutBytes += outChunkPos;
                                outChunkPos = 0;
                            }
                        }
                    }
                    codec.releaseOutputBuffer(outIdx, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        outEos = true;
                    }
                }
            }

            if (outChunkPos > 0) {
                writePcmOrThrow(pcmOut, outChunk, outChunkPos, flushEachBuffer);
                totalOutBytes += outChunkPos;
            }
            if (totalOutBytesRef != null && totalOutBytesRef.length > 0) {
                totalOutBytesRef[0] = totalOutBytes;
            }
            return "";
        } finally {
            try {
                if (codec != null) {
                    codec.stop();
                    codec.release();
                }
            } catch (Throwable ignored) {}
        }
    }

    private static void writePcmOrThrow(OutputStream out, byte[] data, int len, boolean flush)
            throws PcmSinkClosedException {
        try {
            out.write(data, 0, len);
            if (flush) {
                out.flush();
            }
        } catch (IOException ioe) {
            throw new PcmSinkClosedException(ioe);
        }
    }

    private static Map<String, String> parseHeadersJson(String headersJson) {
        Map<String, String> map = new HashMap<>();
        if (headersJson == null || headersJson.trim().isEmpty()) {
            return map;
        }
        try {
            JSONObject obj = new JSONObject(headersJson);
            Iterator<String> keys = obj.keys();
            while (keys.hasNext()) {
                String k = keys.next();
                String v = obj.optString(k, "");
                if (k != null && !v.isEmpty()) {
                    map.put(k, v);
                }
            }
        } catch (Throwable ignored) {}
        return map;
    }

    private static final class PcmSinkClosedException extends Exception {
        PcmSinkClosedException(Throwable cause) { super(cause); }
    }

    private static final class HttpRefusedException extends IOException {
        final int statusCode;
        HttpRefusedException(int statusCode) {
            super("HTTP refused (" + statusCode + ")");
            this.statusCode = statusCode;
        }
    }

    /**
     * Random-access MediaDataSource backed by ranged 512 KiB HTTP GETs with a 12-chunk LRU RAM cache,
     * 2-chunk asynchronous lookahead prefetcher, redirect tracking, and retries so YouTube direct
     * streams start fast and glide smoothly over jitter without disk bloat.
     */
    private static final class RangedHttpMediaDataSource extends MediaDataSource {
        private static final int CHUNK_SIZE = 512 * 1024;
        private static final int MAX_CACHED_CHUNKS = 12;

        private volatile String streamUrl;
        private final Map<String, String> headers;
        private volatile long totalSize;
        private volatile boolean closed = false;
        volatile int refusedStatus = 0;

        private final Object cacheLock = new Object();
        private final Set<Long> inFlight = new HashSet<>();
        private final LinkedHashMap<Long, byte[]> chunkCache =
                new LinkedHashMap<Long, byte[]>(16, 0.75f, true) {
                    @Override
                    protected boolean removeEldestEntry(Map.Entry<Long, byte[]> eldest) {
                        return size() > MAX_CACHED_CHUNKS;
                    }
                };
        private final ExecutorService prefetchExec = Executors.newFixedThreadPool(2, new ThreadFactory() {
            @Override
            public Thread newThread(Runnable r) {
                Thread t = new Thread(r, "WaHttpPrefetch");
                t.setDaemon(true);
                return t;
            }
        });

        RangedHttpMediaDataSource(String url, Map<String, String> headers, long knownSize) throws IOException {
            this.streamUrl = url;
            this.headers = headers != null ? headers : new HashMap<>();
            this.totalSize = knownSize > 0 ? knownSize : -1L;
            // Pre-fetch chunk 0 immediately to fail fast on 403/410, resolve totalSize, and warm chunks 1..2
            try {
                getChunk(0L, true);
            } catch (IOException ioe) {
                close();
                throw ioe;
            }
        }

        @Override
        public long getSize() {
            return totalSize;
        }

        @Override
        public int readAt(long position, byte[] buffer, int offset, int size) throws IOException {
            if (closed || position < 0 || size < 0) {
                return -1;
            }
            if (size == 0) {
                return 0;
            }
            long curTotal = totalSize;
            if (curTotal >= 0 && position >= curTotal) {
                return -1;
            }

            long remainingInFile = (curTotal >= 0) ? (curTotal - position) : size;
            int toRead = (int) Math.min(size, remainingInFile);
            int totalRead = 0;

            while (totalRead < toRead && !closed) {
                long curPos = position + totalRead;
                curTotal = totalSize;
                if (curTotal >= 0 && curPos >= curTotal) {
                    break;
                }
                long chunkIdx = curPos / CHUNK_SIZE;
                int chunkOff = (int) (curPos % CHUNK_SIZE);
                byte[] chunk = getChunk(chunkIdx, true);
                if (chunk == null || chunkOff >= chunk.length) {
                    break;
                }
                int avail = chunk.length - chunkOff;
                int copy = Math.min(avail, toRead - totalRead);
                System.arraycopy(chunk, chunkOff, buffer, offset + totalRead, copy);
                totalRead += copy;
            }

            return (totalRead > 0) ? totalRead : -1;
        }

        private void schedulePrefetchLocked(long baseIdx) {
            if (closed) {
                return;
            }
            long curTotal = totalSize;
            for (long off = 1; off <= 2; off++) {
                final long nextIdx = baseIdx + off;
                long nextStart = nextIdx * (long) CHUNK_SIZE;
                if (curTotal >= 0 && nextStart >= curTotal) {
                    break;
                }
                if (chunkCache.containsKey(nextIdx) || inFlight.contains(nextIdx)) {
                    continue;
                }
                try {
                    prefetchExec.execute(new Runnable() {
                        @Override
                        public void run() {
                            try {
                                getChunk(nextIdx, false);
                            } catch (Throwable ignored) {}
                        }
                    });
                } catch (Throwable ignored) {
                    // Executor already shut down
                }
            }
        }

        private byte[] getChunk(long chunkIdx, boolean triggerPrefetch) throws IOException {
            if (closed) {
                return new byte[0];
            }
            long start = chunkIdx * (long) CHUNK_SIZE;
            long knownTotal = totalSize;
            if (knownTotal >= 0 && start >= knownTotal) {
                return new byte[0];
            }

            boolean weFetch = false;
            synchronized (cacheLock) {
                byte[] cached = chunkCache.get(chunkIdx);
                if (cached != null) {
                    if (triggerPrefetch) {
                        schedulePrefetchLocked(chunkIdx);
                    }
                    return cached;
                }
                while (inFlight.contains(chunkIdx)) {
                    if (closed) {
                        return new byte[0];
                    }
                    try {
                        cacheLock.wait(200);
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        throw new IOException("Interrupted waiting for chunk " + chunkIdx, ie);
                    }
                    cached = chunkCache.get(chunkIdx);
                    if (cached != null) {
                        if (triggerPrefetch) {
                            schedulePrefetchLocked(chunkIdx);
                        }
                        return cached;
                    }
                    if (refusedStatus > 0) {
                        throw new HttpRefusedException(refusedStatus);
                    }
                }
                if (closed) {
                    return new byte[0];
                }
                inFlight.add(chunkIdx);
                weFetch = true;
            }

            try {
                long curTotal = totalSize;
                if (curTotal >= 0 && start >= curTotal) {
                    return new byte[0];
                }
                long end = (curTotal > 0) ? Math.min(start + CHUNK_SIZE - 1, curTotal - 1) : (start + CHUNK_SIZE - 1);
                byte[] data = fetchRangeWithRetry(start, end);
                synchronized (cacheLock) {
                    if (data.length > 0 && !closed) {
                        chunkCache.put(chunkIdx, data);
                    }
                    if (triggerPrefetch && data.length > 0 && !closed) {
                        schedulePrefetchLocked(chunkIdx);
                    }
                }
                return data;
            } finally {
                if (weFetch) {
                    synchronized (cacheLock) {
                        inFlight.remove(chunkIdx);
                        cacheLock.notifyAll();
                    }
                }
            }
        }

        private byte[] fetchRangeWithRetry(long start, long end) throws IOException {
            IOException lastErr = null;
            for (int attempt = 0; attempt < 3 && !closed; attempt++) {
                try {
                    return fetchRangeOnce(start, end);
                } catch (HttpRefusedException refused) {
                    this.refusedStatus = refused.statusCode;
                    throw refused;
                } catch (IOException ioe) {
                    lastErr = ioe;
                    if (attempt < 2 && !closed) {
                        try { Thread.sleep(250L * (attempt + 1)); } catch (InterruptedException ignored) {}
                    }
                }
            }
            throw (lastErr != null) ? lastErr : new IOException("Range request failed at " + start);
        }

        private byte[] fetchRangeOnce(long start, long end) throws IOException {
            String target = this.streamUrl;
            for (int hop = 0; hop < 6 && !closed; hop++) {
                HttpURLConnection conn = (HttpURLConnection) new URL(target).openConnection();
                conn.setInstanceFollowRedirects(false);
                conn.setConnectTimeout(6000);
                conn.setReadTimeout(12000);
                conn.setRequestMethod("GET");
                for (Map.Entry<String, String> e : headers.entrySet()) {
                    conn.setRequestProperty(e.getKey(), e.getValue());
                }
                conn.setRequestProperty("Range", "bytes=" + start + "-" + end);

                try {
                    int code = conn.getResponseCode();
                    if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308) {
                        String loc = conn.getHeaderField("Location");
                        if (loc == null || loc.isEmpty()) {
                            throw new IOException("Redirect without Location header");
                        }
                        target = new URL(new URL(target), loc).toString();
                        this.streamUrl = target;
                        continue;
                    }
                    if (code == 416) {
                        String cr = conn.getHeaderField("Content-Range");
                        parseTotalFromContentRange(cr);
                        return new byte[0];
                    }
                    if (code == 400 || code == 401 || code == 403 || code == 404 || code == 410) {
                        throw new HttpRefusedException(code);
                    }
                    if (code < 200 || code >= 300) {
                        throw new IOException("Unexpected HTTP " + code);
                    }

                    if (code == 206) {
                        parseTotalFromContentRange(conn.getHeaderField("Content-Range"));
                    } else if (code == 200) {
                        long cl = conn.getContentLengthLong();
                        if (cl > 0 && this.totalSize <= 0) {
                            this.totalSize = cl;
                        }
                        if (start > 0) {
                            throw new IOException("Server ignored Range header");
                        }
                    }

                    int expectedMax = (int) Math.min(CHUNK_SIZE, (end - start + 1));
                    ByteArrayOutputStream out = new ByteArrayOutputStream(Math.max(4096, expectedMax));
                    try (InputStream is = conn.getInputStream()) {
                        byte[] tmp = new byte[16384];
                        int r;
                        while (!closed && (r = is.read(tmp)) != -1) {
                            out.write(tmp, 0, r);
                            if (code == 200 && out.size() >= CHUNK_SIZE) {
                                break;
                            }
                        }
                    }
                    return out.toByteArray();
                } finally {
                    conn.disconnect();
                }
            }
            throw closed ? new IOException("DataSource closed") : new IOException("Too many redirects");
        }

        private void parseTotalFromContentRange(String cr) {
            if (cr == null) return;
            int slash = cr.lastIndexOf('/');
            if (slash < 0 || slash + 1 >= cr.length()) return;
            String tail = cr.substring(slash + 1).trim();
            try {
                long t = Long.parseLong(tail);
                if (t > 0) this.totalSize = t;
            } catch (NumberFormatException ignored) {}
        }

        @Override
        public void close() {
            closed = true;
            prefetchExec.shutdownNow();
            synchronized (cacheLock) {
                chunkCache.clear();
                inFlight.clear();
                cacheLock.notifyAll();
            }
        }
    }

    private static int clampS16(float sample) {
        int v = Math.round(sample * 32767.0f);
        if (v > 32767) return 32767;
        if (v < -32767) return -32767;
        return v;
    }

    private interface VideoAuConsumer {
        void onAccessUnit(byte[] annexBAu) throws IOException, PcmSinkClosedException;
    }

    /**
     * Hardware-transcode any Android-decodable video (.mp4/.mkv/.mov) into a lightweight
     * <=maxDim (e.g. 480p), fixed targetFps (e.g. 15fps), 0-B-frame, 1s-keyframe
     * H.264 Annex-B elementary stream ready for WhatsApp's real-time video RTP pipeline.
     *
     * @return "" on success, or error string on failure.
     */
    public static String transcodeVideoToH264(
            String inputPath, String outH264Path, int maxDim, int targetFps, int bitrate) {
        if (maxDim < 160) maxDim = 480;
        if (targetFps <= 0 || targetFps > 30) targetFps = 15;
        if (bitrate <= 100000) bitrate = 700000;

        MediaExtractor extractor = new MediaExtractor();
        BufferedOutputStream bos = null;
        File tmpFile = new File(outH264Path + ".tmp");

        try {
            extractor.setDataSource(inputPath);
            bos = new BufferedOutputStream(new FileOutputStream(tmpFile), 65536);
            final BufferedOutputStream outStream = bos;
            long[] outFrames = new long[1];

            String err = transcodeVideoFromExtractor(
                    extractor,
                    maxDim,
                    targetFps,
                    bitrate,
                    600_000_000L,
                    null,
                    outFrames,
                    new VideoAuConsumer() {
                        @Override
                        public void onAccessUnit(byte[] annexBAu) throws IOException {
                            outStream.write(annexBAu);
                        }
                    });
            if (!err.isEmpty()) {
                return err;
            }

            bos.flush();
            bos.close();
            bos = null;

            if (outFrames[0] == 0 || !tmpFile.exists() || tmpFile.length() == 0) {
                return "Hardware encoder produced 0 video frames";
            }

            File finalFile = new File(outH264Path);
            if (finalFile.exists()) {
                //noinspection ResultOfMethodCallIgnored
                finalFile.delete();
            }
            if (!tmpFile.renameTo(finalFile)) {
                return "Failed to rename transcoded H.264 file";
            }
            return "";
        } catch (Throwable t) {
            return "Java video transcode failed: " + t.getMessage();
        } finally {
            try { if (bos != null) bos.close(); } catch (Throwable ignored) {}
            try { extractor.release(); } catch (Throwable ignored) {}
            if (tmpFile.exists()) {
                //noinspection ResultOfMethodCallIgnored
                tmpFile.delete();
            }
        }
    }

    /**
     * Stream remote YouTube/HTTP video + audio URLs directly into Go's loopback TCP sockets
     * (length-prefixed Annex-B H.264 AUs on videoPort, raw 16kHz mono s16le PCM on audioPort)
     * with zero full-file disk download.
     *
     * @return "" on normal EOF or caller-initiated socket stop; error description otherwise.
     */
    public static String streamAvUrlsToSockets(
            String videoUrl,
            String audioUrl,
            String headersJson,
            long videoContentLength,
            long audioContentLength,
            int videoPort,
            int audioPort,
            int maxDim,
            int targetFps,
            int bitrate,
            int maxSeconds) {
        if (maxDim < 160) maxDim = 480;
        if (targetFps <= 0 || targetFps > 30) targetFps = 15;
        if (bitrate <= 100000) bitrate = 650000;

        final String effectiveAudioUrl = (audioUrl != null && !audioUrl.isEmpty()) ? audioUrl : videoUrl;
        final AtomicBoolean cancelFlag = new AtomicBoolean(false);
        final String[] audioErrHolder = new String[]{""};
        Thread audioThread = null;

        if (audioPort > 0 && effectiveAudioUrl != null && !effectiveAudioUrl.isEmpty()) {
            audioThread = new Thread(new Runnable() {
                @Override
                public void run() {
                    String aErr = streamUrlToPcmSocket(
                            effectiveAudioUrl,
                            headersJson,
                            audioContentLength,
                            "127.0.0.1",
                            audioPort,
                            maxSeconds);
                    audioErrHolder[0] = (aErr != null) ? aErr : "";
                    String ast = STREAM_STATES.get(audioPort);
                    if (!audioErrHolder[0].isEmpty() || "stopped".equals(ast)) {
                        cancelFlag.set(true);
                    }
                }
            }, "WaLiveAudioFeeder");
            audioThread.setDaemon(true);
            audioThread.start();
        }

        Map<String, String> headers = parseHeadersJson(headersJson);
        RangedHttpMediaDataSource videoSource = null;
        MediaExtractor extractor = new MediaExtractor();
        Socket videoSocket = null;
        DataOutputStream videoDos = null;
        STREAM_STATES.put(videoPort, "connecting");
        boolean cleanVideoExit = false;

        try {
            videoSource = new RangedHttpMediaDataSource(videoUrl, headers, videoContentLength);
            extractor.setDataSource(videoSource);

            videoSocket = new Socket();
            videoSocket.connect(new InetSocketAddress("127.0.0.1", videoPort), 5000);
            videoSocket.setTcpNoDelay(true);
            videoSocket.setSoTimeout(0);
            videoDos = new DataOutputStream(new BufferedOutputStream(videoSocket.getOutputStream(), 32768));

            final DataOutputStream dosRef = videoDos;
            final boolean[] markedStreaming = new boolean[1];
            long[] outFrames = new long[1];
            long maxPtsUs = (maxSeconds > 0) ? (maxSeconds * 1_000_000L) : Long.MAX_VALUE;

            String err = transcodeVideoFromExtractor(
                    extractor,
                    maxDim,
                    targetFps,
                    bitrate,
                    maxPtsUs,
                    cancelFlag,
                    outFrames,
                    new VideoAuConsumer() {
                        @Override
                        public void onAccessUnit(byte[] annexBAu) throws PcmSinkClosedException {
                            if (annexBAu == null || annexBAu.length == 0) {
                                return;
                            }
                            try {
                                dosRef.writeInt(annexBAu.length);
                                dosRef.write(annexBAu);
                                dosRef.flush();
                                if (!markedStreaming[0]) {
                                    markedStreaming[0] = true;
                                    STREAM_STATES.put(videoPort, "streaming");
                                }
                            } catch (IOException ioe) {
                                throw new PcmSinkClosedException(ioe);
                            }
                        }
                    });

            if (!err.isEmpty()) {
                STREAM_STATES.put(videoPort, "error:" + err);
                return err;
            }
            if (!audioErrHolder[0].isEmpty()) {
                STREAM_STATES.put(videoPort, "error:" + audioErrHolder[0]);
                return audioErrHolder[0];
            }
            if (outFrames[0] == 0 && !cancelFlag.get()) {
                String zeroErr = "Hardware encoder produced 0 video frames";
                STREAM_STATES.put(videoPort, "error:" + zeroErr);
                return zeroErr;
            }
            videoDos.flush();
            cleanVideoExit = true;
            STREAM_STATES.put(videoPort, "done");
            return "";
        } catch (PcmSinkClosedException stopClosed) {
            cancelFlag.set(true);
            STREAM_STATES.put(videoPort, "stopped");
            return !audioErrHolder[0].isEmpty() ? audioErrHolder[0] : "";
        } catch (HttpRefusedException refused) {
            cancelFlag.set(true);
            String err = "HTTP_REFUSED:" + refused.statusCode;
            STREAM_STATES.put(videoPort, "error:" + err);
            return err;
        } catch (Throwable t) {
            cancelFlag.set(true);
            if (videoSource != null && videoSource.refusedStatus > 0) {
                String err = "HTTP_REFUSED:" + videoSource.refusedStatus;
                STREAM_STATES.put(videoPort, "error:" + err);
                return err;
            }
            if (!audioErrHolder[0].isEmpty()) {
                STREAM_STATES.put(videoPort, "error:" + audioErrHolder[0]);
                return audioErrHolder[0];
            }
            String msg = t.getMessage();
            String err = "Live video stream failed: " + (msg != null ? msg : t.getClass().getSimpleName());
            STREAM_STATES.put(videoPort, "error:" + err);
            return err;
        } finally {
            try { if (videoDos != null) videoDos.close(); } catch (Throwable ignored) {}
            try { if (videoSocket != null) videoSocket.close(); } catch (Throwable ignored) {}
            try { extractor.release(); } catch (Throwable ignored) {}
            try { if (videoSource != null) videoSource.close(); } catch (Throwable ignored) {}
            if (audioThread != null) {
                try {
                    audioThread.join(cleanVideoExit && !cancelFlag.get() ? 15000L : 300L);
                } catch (InterruptedException ignored) {}
            }
        }
    }

    private static String transcodeVideoFromExtractor(
            MediaExtractor extractor,
            int maxDim,
            int targetFps,
            int bitrate,
            long maxPtsUs,
            AtomicBoolean cancelFlag,
            long[] outFrameCountRef,
            VideoAuConsumer consumer) throws Exception {
        MediaCodec decoder = null;
        MediaCodec encoder = null;
        Surface decSurface = null;
        Surface encSurface = null;
        SurfaceTexture surfaceTex = null;
        HandlerThread cbThread = null;
        EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
        EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
        EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;

        try {
            int trackIdx = -1;
            MediaFormat inFmt = null;
            String mime = "";
            int count = extractor.getTrackCount();
            for (int i = 0; i < count; i++) {
                MediaFormat fmt = extractor.getTrackFormat(i);
                String m = fmt.getString(MediaFormat.KEY_MIME);
                if (m != null && m.startsWith("video/")) {
                    trackIdx = i;
                    inFmt = fmt;
                    mime = m;
                    break;
                }
            }
            if (trackIdx < 0 || inFmt == null) {
                return "No video track found in media";
            }

            extractor.selectTrack(trackIdx);
            int srcW = inFmt.containsKey(MediaFormat.KEY_WIDTH) ? inFmt.getInteger(MediaFormat.KEY_WIDTH) : 640;
            int srcH = inFmt.containsKey(MediaFormat.KEY_HEIGHT) ? inFmt.getInteger(MediaFormat.KEY_HEIGHT) : 480;
            int rot = inFmt.containsKey(MediaFormat.KEY_ROTATION) ? inFmt.getInteger(MediaFormat.KEY_ROTATION) : 0;

            int dispW = (rot == 90 || rot == 270) ? srcH : srcW;
            int dispH = (rot == 90 || rot == 270) ? srcW : srcH;
            double scale = Math.min(1.0, (double) maxDim / Math.max(dispW, dispH));
            int encW = Math.max(160, ((int) Math.round(dispW * scale) + 8) & ~15);
            int encH = Math.max(160, ((int) Math.round(dispH * scale) + 8) & ~15);

            // Prevent decoder from auto-rotating differently than rotation-key check
            if (inFmt.containsKey(MediaFormat.KEY_ROTATION)) {
                inFmt.setInteger(MediaFormat.KEY_ROTATION, 0);
            }

            MediaFormat encFmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, encW, encH);
            encFmt.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            encFmt.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
            encFmt.setInteger(MediaFormat.KEY_FRAME_RATE, targetFps);
            encFmt.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            try {
                encFmt.setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline);
                encFmt.setInteger(MediaFormat.KEY_LEVEL, MediaCodecInfo.CodecProfileLevel.AVCLevel31);
            } catch (Throwable ignored) {}
            try {
                encFmt.setInteger("max-bframes", 0);
            } catch (Throwable ignored) {}
            try {
                encFmt.setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_VBR);
            } catch (Throwable ignored) {}

            encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC);
            try {
                encoder.configure(encFmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            } catch (Throwable profileErr) {
                MediaFormat fallbackFmt = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, encW, encH);
                fallbackFmt.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
                fallbackFmt.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
                fallbackFmt.setInteger(MediaFormat.KEY_FRAME_RATE, targetFps);
                fallbackFmt.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
                encoder.reset();
                encoder.configure(fallbackFmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            }
            encSurface = encoder.createInputSurface();
            encoder.start();

            // Setup EGL14 + GLES20 SurfaceTexture bridge (decoder -> GPU scale/rotate -> encoder)
            eglDisplay = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
            if (eglDisplay == EGL14.EGL_NO_DISPLAY) {
                return "Unable to get EGL14 display";
            }
            int[] vers = new int[2];
            if (!EGL14.eglInitialize(eglDisplay, vers, 0, vers, 1)) {
                return "Unable to initialize EGL14";
            }
            int[] cfgAttr = {
                    EGL14.EGL_RED_SIZE, 8,
                    EGL14.EGL_GREEN_SIZE, 8,
                    EGL14.EGL_BLUE_SIZE, 8,
                    EGL14.EGL_ALPHA_SIZE, 8,
                    EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                    0x3142 /* EGL_RECORDABLE_ANDROID */, 1,
                    EGL14.EGL_NONE
            };
            EGLConfig[] configs = new EGLConfig[1];
            int[] numConfigs = new int[1];
            if (!EGL14.eglChooseConfig(eglDisplay, cfgAttr, 0, configs, 0, 1, numConfigs, 0) || numConfigs[0] == 0) {
                return "Unable to choose EGLConfig";
            }
            int[] ctxAttr = {EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE};
            eglContext = EGL14.eglCreateContext(eglDisplay, configs[0], EGL14.EGL_NO_CONTEXT, ctxAttr, 0);
            int[] surfAttr = {EGL14.EGL_NONE};
            eglSurface = EGL14.eglCreateWindowSurface(eglDisplay, configs[0], encSurface, surfAttr, 0);
            if (!EGL14.eglMakeCurrent(eglDisplay, eglSurface, eglSurface, eglContext)) {
                return "eglMakeCurrent failed";
            }

            GlRenderer renderer = new GlRenderer(rot);
            cbThread = new HandlerThread("WaMediaCodecGL");
            cbThread.start();
            Handler cbHandler = new Handler(cbThread.getLooper());
            surfaceTex = new SurfaceTexture(renderer.texId);
            final Object frameLock = new Object();
            final boolean[] frameReady = new boolean[1];
            surfaceTex.setOnFrameAvailableListener(new SurfaceTexture.OnFrameAvailableListener() {
                @Override
                public void onFrameAvailable(SurfaceTexture st) {
                    synchronized (frameLock) {
                        frameReady[0] = true;
                        frameLock.notifyAll();
                    }
                }
            }, cbHandler);
            decSurface = new Surface(surfaceTex);

            decoder = MediaCodec.createDecoderByType(mime);
            decoder.configure(inFmt, decSurface, null, 0);
            decoder.start();

            MediaCodec.BufferInfo decInfo = new MediaCodec.BufferInfo();
            MediaCodec.BufferInfo encInfo = new MediaCodec.BufferInfo();
            boolean inEos = false;
            boolean decEos = false;
            boolean encEos = false;
            boolean sentEndOfInput = false;
            boolean sentFirstFrame = false;

            final long frameIntervalUs = 1_000_000L / targetFps;
            final long frameIntervalNs = 1_000_000_000L / targetFps;
            long nextCapturePtsUs = -1L;
            long outFrameIdx = 0;
            byte[] csdConfig = null;
            int encIdleTicks = 0;
            ByteArrayOutputStream partialAu = new ByteArrayOutputStream();
            boolean partialIsKey = false;

            while (!encEos) {
                if (cancelFlag != null && cancelFlag.get()) {
                    break;
                }
                if (decEos && ++encIdleTicks > 600) {
                    break;
                }
                if (!inEos) {
                    int inIdx = decoder.dequeueInputBuffer(5000);
                    if (inIdx >= 0) {
                        ByteBuffer inBuf = decoder.getInputBuffer(inIdx);
                        int sz = inBuf != null ? extractor.readSampleData(inBuf, 0) : -1;
                        long pts = extractor.getSampleTime();
                        if (sz < 0 || (pts >= 0 && pts > maxPtsUs)) {
                            decoder.queueInputBuffer(inIdx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                            inEos = true;
                        } else {
                            decoder.queueInputBuffer(inIdx, 0, sz, Math.max(0, pts), 0);
                            extractor.advance();
                        }
                    }
                }

                if (!decEos) {
                    int decOut = decoder.dequeueOutputBuffer(decInfo, 5000);
                    if (decOut >= 0) {
                        boolean isEos = (decInfo.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                        boolean doRender = false;
                        if (decInfo.size > 0) {
                            if (nextCapturePtsUs < 0) {
                                nextCapturePtsUs = decInfo.presentationTimeUs;
                            }
                            if (decInfo.presentationTimeUs >= nextCapturePtsUs) {
                                doRender = true;
                                while (nextCapturePtsUs <= decInfo.presentationTimeUs) {
                                    nextCapturePtsUs += frameIntervalUs;
                                }
                            }
                        }
                        if (doRender) {
                            synchronized (frameLock) {
                                frameReady[0] = false;
                            }
                            decoder.releaseOutputBuffer(decOut, true);
                            synchronized (frameLock) {
                                long deadline = System.currentTimeMillis() + 300;
                                while (!frameReady[0]) {
                                    long rem = deadline - System.currentTimeMillis();
                                    if (rem <= 0) break;
                                    frameLock.wait(rem);
                                }
                            }
                            surfaceTex.updateTexImage();
                            GLES20.glViewport(0, 0, encW, encH);
                            renderer.draw(surfaceTex);
                            EGLExt.eglPresentationTimeANDROID(eglDisplay, eglSurface, outFrameIdx * frameIntervalNs);
                            EGL14.eglSwapBuffers(eglDisplay, eglSurface);
                            outFrameIdx++;
                        } else {
                            decoder.releaseOutputBuffer(decOut, false);
                        }
                        if (isEos) {
                            decEos = true;
                            if (!sentEndOfInput) {
                                encoder.signalEndOfInputStream();
                                sentEndOfInput = true;
                            }
                        }
                    }
                }

                // Drain encoded H.264 access units cleanly
                while (true) {
                    if (cancelFlag != null && cancelFlag.get()) {
                        break;
                    }
                    int encOut = encoder.dequeueOutputBuffer(encInfo, 2000);
                    if (encOut == MediaCodec.INFO_TRY_AGAIN_LATER) {
                        break;
                    } else if (encOut == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        MediaFormat of = encoder.getOutputFormat();
                        byte[] sps = readCsd(of, "csd-0");
                        byte[] pps = readCsd(of, "csd-1");
                        if (sps != null && sps.length > 0) {
                            csdConfig = ensureAnnexB(combineCsd(sps, pps));
                        }
                    } else if (encOut >= 0) {
                        encIdleTicks = 0;
                        ByteBuffer outBuf = encoder.getOutputBuffer(encOut);
                        if (outBuf != null && encInfo.size > 0) {
                            outBuf.position(encInfo.offset);
                            outBuf.limit(encInfo.offset + encInfo.size);
                            byte[] raw = new byte[encInfo.size];
                            outBuf.get(raw);
                            byte[] annex = ensureAnnexB(raw);

                            if ((encInfo.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                                csdConfig = annex;
                            } else {
                                boolean isKey = (encInfo.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0;
                                boolean isPartial = (encInfo.flags & MediaCodec.BUFFER_FLAG_PARTIAL_FRAME) != 0;
                                if (isPartial || partialAu.size() > 0) {
                                    partialAu.write(annex);
                                    partialIsKey |= isKey;
                                }
                                if (!isPartial) {
                                    byte[] completeAu = (partialAu.size() > 0) ? partialAu.toByteArray() : annex;
                                    boolean auKey = (partialAu.size() > 0) ? partialIsKey : isKey;
                                    partialAu.reset();
                                    partialIsKey = false;

                                    if ((auKey || !sentFirstFrame) && csdConfig != null && !startsWithSps(completeAu)) {
                                        completeAu = combineCsd(csdConfig, completeAu);
                                    }
                                    sentFirstFrame = true;
                                    consumer.onAccessUnit(completeAu);
                                }
                            }
                        }
                        encoder.releaseOutputBuffer(encOut, false);
                        if ((encInfo.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                            encEos = true;
                            break;
                        }
                    }
                }
            }

            if (outFrameCountRef != null && outFrameCountRef.length > 0) {
                outFrameCountRef[0] = outFrameIdx;
            }
            return "";
        } finally {
            try { if (decoder != null) { decoder.stop(); decoder.release(); } } catch (Throwable ignored) {}
            try { if (encoder != null) { encoder.stop(); encoder.release(); } } catch (Throwable ignored) {}
            try { if (decSurface != null) decSurface.release(); } catch (Throwable ignored) {}
            try { if (surfaceTex != null) surfaceTex.release(); } catch (Throwable ignored) {}
            try { if (cbThread != null) cbThread.quitSafely(); } catch (Throwable ignored) {}
            if (eglDisplay != EGL14.EGL_NO_DISPLAY) {
                try {
                    EGL14.eglMakeCurrent(eglDisplay, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                    if (eglSurface != EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(eglDisplay, eglSurface);
                    if (eglContext != EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(eglDisplay, eglContext);
                    EGL14.eglReleaseThread();
                    EGL14.eglTerminate(eglDisplay);
                } catch (Throwable ignored) {}
            }
            try { if (encSurface != null) encSurface.release(); } catch (Throwable ignored) {}
        }
    }

    private static byte[] readCsd(MediaFormat fmt, String key) {
        if (!fmt.containsKey(key)) return null;
        ByteBuffer bb = fmt.getByteBuffer(key);
        if (bb == null) return null;
        bb.rewind();
        byte[] b = new byte[bb.remaining()];
        bb.get(b);
        return b;
    }

    private static byte[] combineCsd(byte[] a, byte[] b) {
        if (b == null || b.length == 0) return a;
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    private static byte[] ensureAnnexB(byte[] raw) {
        if (raw == null || raw.length == 0) return new byte[0];
        if ((raw.length >= 4 && raw[0] == 0 && raw[1] == 0 && raw[2] == 0 && raw[3] == 1)
                || (raw.length >= 3 && raw[0] == 0 && raw[1] == 0 && raw[2] == 1)) {
            return raw;
        }
        byte[] out = new byte[4 + raw.length];
        out[0] = 0; out[1] = 0; out[2] = 0; out[3] = 1;
        System.arraycopy(raw, 0, out, 4, raw.length);
        return out;
    }

    private static boolean startsWithSps(byte[] annex) {
        if (annex.length >= 5 && annex[0] == 0 && annex[1] == 0 && annex[2] == 0 && annex[3] == 1) {
            return (annex[4] & 0x1F) == 7;
        }
        if (annex.length >= 4 && annex[0] == 0 && annex[1] == 0 && annex[2] == 1) {
            return (annex[3] & 0x1F) == 7;
        }
        return false;
    }

    /** Minimal OpenGL ES 2.0 OES external texture renderer for SurfaceTexture -> MediaCodec Surface. */
    private static final class GlRenderer {
        private static final String VS =
                "uniform mat4 uMVP;\n" +
                "uniform mat4 uST;\n" +
                "attribute vec4 aPos;\n" +
                "attribute vec4 aTex;\n" +
                "varying vec2 vTex;\n" +
                "void main() {\n" +
                "  gl_Position = uMVP * aPos;\n" +
                "  vTex = (uST * aTex).xy;\n" +
                "}\n";

        private static final String FS =
                "#extension GL_OES_EGL_image_external : require\n" +
                "precision mediump float;\n" +
                "varying vec2 vTex;\n" +
                "uniform samplerExternalOES sTex;\n" +
                "void main() {\n" +
                "  gl_FragColor = texture2D(sTex, vTex);\n" +
                "}\n";

        private static final float[] QUAD = {
                -1f, -1f, 0f, 0f, 0f,
                 1f, -1f, 0f, 1f, 0f,
                -1f,  1f, 0f, 0f, 1f,
                 1f,  1f, 0f, 1f, 1f,
        };

        final int texId;
        private final int program;
        private final int aPos;
        private final int aTex;
        private final int uMVP;
        private final int uST;
        private final FloatBuffer vbuf;
        private final float[] mvp = new float[16];
        private final float[] stMat = new float[16];

        GlRenderer(int rotationDeg) {
            vbuf = ByteBuffer.allocateDirect(QUAD.length * 4).order(ByteOrder.nativeOrder()).asFloatBuffer();
            vbuf.put(QUAD).position(0);

            int vs = loadShader(GLES20.GL_VERTEX_SHADER, VS);
            int fs = loadShader(GLES20.GL_FRAGMENT_SHADER, FS);
            program = GLES20.glCreateProgram();
            GLES20.glAttachShader(program, vs);
            GLES20.glAttachShader(program, fs);
            GLES20.glLinkProgram(program);

            aPos = GLES20.glGetAttribLocation(program, "aPos");
            aTex = GLES20.glGetAttribLocation(program, "aTex");
            uMVP = GLES20.glGetUniformLocation(program, "uMVP");
            uST = GLES20.glGetUniformLocation(program, "uST");

            int[] tex = new int[1];
            GLES20.glGenTextures(1, tex, 0);
            texId = tex[0];
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texId);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE);
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE);

            Matrix.setIdentityM(mvp, 0);
            if (rotationDeg != 0) {
                Matrix.rotateM(mvp, 0, -rotationDeg, 0f, 0f, 1f);
            }
        }

        void draw(SurfaceTexture st) {
            st.getTransformMatrix(stMat);
            GLES20.glClearColor(0f, 0f, 0f, 1f);
            GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT);
            GLES20.glUseProgram(program);

            GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, texId);

            vbuf.position(0);
            GLES20.glVertexAttribPointer(aPos, 3, GLES20.GL_FLOAT, false, 20, vbuf);
            GLES20.glEnableVertexAttribArray(aPos);
            vbuf.position(3);
            GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 20, vbuf);
            GLES20.glEnableVertexAttribArray(aTex);

            GLES20.glUniformMatrix4fv(uMVP, 1, false, mvp, 0);
            GLES20.glUniformMatrix4fv(uST, 1, false, stMat, 0);
            GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
            GLES20.glFinish();
        }

        private static int loadShader(int type, String src) {
            int sh = GLES20.glCreateShader(type);
            GLES20.glShaderSource(sh, src);
            GLES20.glCompileShader(sh);
            return sh;
        }
    }
}
