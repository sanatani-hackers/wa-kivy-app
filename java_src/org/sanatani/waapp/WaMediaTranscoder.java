package org.sanatani.waapp;

import android.graphics.SurfaceTexture;
import android.media.AudioFormat;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
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

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;

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
        MediaCodec codec = null;
        BufferedOutputStream bos = null;
        File tmpFile = new File(outWavPath + ".tmp");

        try {
            extractor.setDataSource(inputPath);
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
                return "No audio track found in media file";
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

            bos = new BufferedOutputStream(new FileOutputStream(tmpFile), 32768);
            byte[] emptyHdr = new byte[44];
            bos.write(emptyHdr);

            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean inEos = false;
            boolean outEos = false;
            long totalOutBytes = 0;

            // Stateful linear resampler across output buffers -> exact 16000 Hz mono
            final double targetRate = 16000.0;
            double pos = 0.0;
            float prevMono = 0.0f;
            boolean havePrev = false;
            byte[] rawScratch = new byte[65536];
            byte[] outPair = new byte[2];

            // Cap audio decode to 10 minutes (same as video cap)
            final long maxPtsUs = 600_000_000L;

            while (!outEos) {
                if (!inEos) {
                    int inIdx = codec.dequeueInputBuffer(5000);
                    if (inIdx >= 0) {
                        ByteBuffer inBuf = codec.getInputBuffer(inIdx);
                        int sz = inBuf != null ? extractor.readSampleData(inBuf, 0) : -1;
                        long pts = extractor.getSampleTime();
                        if (sz < 0 || pts > maxPtsUs) {
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
                                    outPair[0] = (byte) (s16 & 0xFF);
                                    outPair[1] = (byte) ((s16 >> 8) & 0xFF);
                                    bos.write(outPair, 0, 2);
                                    totalOutBytes += 2;
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
                                    float sample = (float) (s0 * (1.0 - frac) + s1 * frac);
                                    int s16 = clampS16(sample);
                                    outPair[0] = (byte) (s16 & 0xFF);
                                    outPair[1] = (byte) ((s16 >> 8) & 0xFF);
                                    bos.write(outPair, 0, 2);
                                    totalOutBytes += 2;
                                    pos += step;
                                }
                                pos -= frames;
                                prevMono = mono[frames - 1];
                                havePrev = true;
                            }
                        }
                    }
                    codec.releaseOutputBuffer(outIdx, false);
                    if ((info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0) {
                        outEos = true;
                    }
                }
            }

            bos.flush();
            bos.close();
            bos = null;

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
            try { if (codec != null) { codec.stop(); codec.release(); } } catch (Throwable ignored) {}
            try { extractor.release(); } catch (Throwable ignored) {}
            if (tmpFile.exists()) {
                //noinspection ResultOfMethodCallIgnored
                tmpFile.delete();
            }
        }
    }

    private static int clampS16(float sample) {
        int v = Math.round(sample * 32767.0f);
        if (v > 32767) return 32767;
        if (v < -32767) return -32767;
        return v;
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
        MediaCodec decoder = null;
        MediaCodec encoder = null;
        Surface decSurface = null;
        Surface encSurface = null;
        SurfaceTexture surfaceTex = null;
        HandlerThread cbThread = null;
        EGLDisplay eglDisplay = EGL14.EGL_NO_DISPLAY;
        EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
        EGLSurface eglSurface = EGL14.EGL_NO_SURFACE;
        BufferedOutputStream bos = null;
        File tmpFile = new File(outH264Path + ".tmp");

        try {
            extractor.setDataSource(inputPath);
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
                return "No video track found in file";
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
                // Retry with minimal compatible format if device rejects profile/level keys
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
            surfaceTex.setOnFrameAvailableListener(st -> {
                synchronized (frameLock) {
                    frameReady[0] = true;
                    frameLock.notifyAll();
                }
            }, cbHandler);
            decSurface = new Surface(surfaceTex);

            decoder = MediaCodec.createDecoderByType(mime);
            decoder.configure(inFmt, decSurface, null, 0);
            decoder.start();

            bos = new BufferedOutputStream(new FileOutputStream(tmpFile), 65536);

            MediaCodec.BufferInfo decInfo = new MediaCodec.BufferInfo();
            MediaCodec.BufferInfo encInfo = new MediaCodec.BufferInfo();
            boolean inEos = false;
            boolean decEos = false;
            boolean encEos = false;
            boolean sentEndOfInput = false;

            final long frameIntervalUs = 1_000_000L / targetFps;
            final long frameIntervalNs = 1_000_000_000L / targetFps;
            final long maxPtsUs = 600_000_000L; // 10 min safety cap
            long nextCapturePtsUs = -1L;
            long outFrameIdx = 0;
            byte[] csdConfig = null;
            int encIdleTicks = 0;

            while (!encEos) {
                if (decEos && ++encIdleTicks > 600) {
                    break;
                }
                if (!inEos) {
                    int inIdx = decoder.dequeueInputBuffer(5000);
                    if (inIdx >= 0) {
                        ByteBuffer inBuf = decoder.getInputBuffer(inIdx);
                        int sz = inBuf != null ? extractor.readSampleData(inBuf, 0) : -1;
                        long pts = extractor.getSampleTime();
                        if (sz < 0 || pts > maxPtsUs) {
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

                // Drain encoded H.264 packets cleanly
                while (true) {
                    int encOut = encoder.dequeueOutputBuffer(encInfo, 2000);
                    if (encOut == MediaCodec.INFO_TRY_AGAIN_LATER) {
                        break;
                    } else if (encOut == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        MediaFormat of = encoder.getOutputFormat();
                        byte[] sps = readCsd(of, "csd-0");
                        byte[] pps = readCsd(of, "csd-1");
                        if (sps != null && sps.length > 0) {
                            byte[] comb = combineCsd(sps, pps);
                            csdConfig = ensureAnnexB(comb);
                            bos.write(csdConfig);
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
                                bos.write(annex);
                            } else {
                                if ((encInfo.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0 && csdConfig != null && !startsWithSps(annex)) {
                                    bos.write(csdConfig);
                                }
                                bos.write(annex);
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

            bos.flush();
            bos.close();
            bos = null;

            if (outFrameIdx == 0 || !tmpFile.exists() || tmpFile.length() == 0) {
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
            try { extractor.release(); } catch (Throwable ignored) {}
            if (tmpFile.exists()) {
                //noinspection ResultOfMethodCallIgnored
                tmpFile.delete();
            }
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
