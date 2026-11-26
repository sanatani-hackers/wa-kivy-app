"""Media discovery, built-in hold chimes, and Android/FFmpeg transcoding helpers for WhatsApp calls."""
import logging
import math
import os
import shutil
import struct
import subprocess
import wave
from typing import Optional

from common import plugin_media_dir

log = logging.getLogger("wa.call_media")

AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".opus")
VIDEO_EXTS = (".h264", ".mp4", ".mkv", ".mov")
EXTRA_AUDIO_EXTS = (".m4a", ".aac", ".flac")


def call_music_dir() -> str:
    """App-external directory for call music tracks and video clips."""
    return plugin_media_dir("call_music")


def call_cache_dir() -> str:
    """Cache directory for extracted H.264 streams and converted WAV files."""
    d = os.path.join(call_music_dir(), ".cache")
    os.makedirs(d, exist_ok=True)
    return d


def call_recordings_dir() -> str:
    """Directory for recorded caller audio (.wav) and video (.h264) captures."""
    return plugin_media_dir("call_recordings")


def _write_synth_wav(path: str, notes: list[tuple[float, float]], sample_rate: int = 16000) -> None:
    """Synthesize a smooth polyphonic harmonic chime WAV track without external assets."""
    pcm = bytearray()
    for freq, dur in notes:
        total = int(sample_rate * dur)
        attack = max(1, int(sample_rate * 0.04))
        release = max(1, int(sample_rate * 0.14))
        for i in range(total):
            t = i / sample_rate
            if freq <= 0:
                val = 0.0
            else:
                # Warm fundamental + fifth + soft octave shimmer
                wave_val = (
                    0.58 * math.sin(2.0 * math.pi * freq * t)
                    + 0.24 * math.sin(2.0 * math.pi * (freq * 1.5) * t)
                    + 0.12 * math.sin(2.0 * math.pi * (freq * 2.0) * t)
                )
                env = 1.0
                if i < attack:
                    env = i / attack
                elif i > total - release:
                    env = max(0.0, (total - i) / release)
                val = wave_val * env * 0.42
            sample = max(-32767, min(32767, int(val * 32767)))
            pcm.extend(struct.pack("<h", sample))

    tmp = f"{path}.tmp"
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm))
    os.replace(tmp, path)


def ensure_builtin_tracks() -> list[str]:
    """Create default soothing hold-music chimes if call_music directory has no starter WAVs."""
    music_dir = call_music_dir()
    created = []
    tracks = {
        "hold_chime.wav": [
            (261.63, 0.55), (329.63, 0.55), (392.00, 0.55), (523.25, 0.85),
            (0.0, 0.35),
            (293.66, 0.55), (349.23, 0.55), (440.00, 0.55), (587.33, 0.85),
            (0.0, 0.45),
        ],
        "ambient_lounge.wav": [
            (220.00, 0.65), (261.63, 0.65), (329.63, 0.65), (392.00, 1.00),
            (0.0, 0.30),
            (196.00, 0.65), (246.94, 0.65), (293.66, 0.65), (392.00, 1.00),
            (0.0, 0.40),
        ],
    }
    for fname, notes in tracks.items():
        p = os.path.join(music_dir, fname)
        if not os.path.exists(p):
            try:
                _write_synth_wav(p, notes)
                created.append(p)
            except Exception as e:
                log.debug("failed creating built-in track %s: %s", fname, e)
    return created


def list_media_tracks(include_public: bool = True) -> dict[str, list[str]]:
    """Discover audio and video files available for call playback."""
    ensure_builtin_tracks()
    dirs = [call_music_dir()]
    if include_public:
        for d in ("/sdcard/Download", "/sdcard/Music", "/sdcard/Download/WA_Music"):
            if os.path.isdir(d):
                dirs.append(d)

    audio_files: list[str] = []
    video_files: list[str] = []
    seen: set[str] = set()

    all_audio = AUDIO_EXTS + EXTRA_AUDIO_EXTS
    for base in dirs:
        try:
            entries = sorted(os.listdir(base))
        except Exception:
            continue
        for fn in entries:
            if fn.startswith("."):
                continue
            full = os.path.join(base, fn)
            if not os.path.isfile(full) or full in seen:
                continue
            low = fn.lower()
            if low.endswith(all_audio):
                seen.add(full)
                audio_files.append(full)
            elif low.endswith(VIDEO_EXTS):
                seen.add(full)
                video_files.append(full)

    return {"audio": audio_files, "video": video_files}


def resolve_track_path(query: str, media_type: str = "audio") -> Optional[str]:
    """Resolve an exact file path, filename, or substring query against known media folders."""
    q = (query or "").strip().strip("'\"")
    if not q:
        tracks = list_media_tracks()
        items = tracks["video"] if media_type == "video" else tracks["audio"]
        return items[0] if items else None

    if os.path.isfile(q):
        return q

    candidates = [
        os.path.join(call_music_dir(), q),
        os.path.join("/sdcard/Download", q),
        os.path.join("/sdcard/Music", q),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    tracks = list_media_tracks()
    pool = tracks["video"] if media_type == "video" else tracks["audio"]
    ql = q.lower()
    for p in pool:
        if os.path.basename(p).lower() == ql:
            return p
    for p in pool:
        if ql in os.path.basename(p).lower():
            return p
    return None


def _is_native_go_audio(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            hdr = f.read(16)
        if hdr[:4] == b"RIFF" and hdr[8:12] == b"WAVE":
            return True
        if hdr[:4] == b"OggS" or hdr[:3] == b"ID3":
            return True
        if len(hdr) >= 2 and hdr[0] == 0xFF and (hdr[1] & 0xE0) == 0xE0:
            return True
    except Exception:
        pass
    return False


def _android_extract_h264_from_mp4(mp4_path: str, out_h264_path: str) -> tuple[bool, str]:
    """Extract raw Annex-B H.264 video stream from an MP4 file using native Android MediaExtractor."""
    try:
        from jnius import autoclass
        MediaExtractor = autoclass("android.media.MediaExtractor")
        ByteBuffer = autoclass("java.nio.ByteBuffer")
    except Exception as e:
        return False, f"Android MediaExtractor unavailable: {e}"

    extractor = MediaExtractor()
    try:
        extractor.setDataSource(mp4_path)
        v_track = -1
        v_format = None
        for idx in range(extractor.getTrackCount()):
            fmt = extractor.getTrackFormat(idx)
            mime = str(fmt.getString("mime") or "")
            if mime.startswith("video/avc") or mime.startswith("video/h264"):
                v_track = idx
                v_format = fmt
                break
        if v_track < 0 or v_format is None:
            return False, "No H.264/AVC video track found in file."

        extractor.selectTrack(v_track)

        def _read_bb(bb) -> bytes:
            if bb is None:
                return b""
            bb.rewind()
            rem = bb.remaining()
            arr = bytearray(rem)
            for i in range(rem):
                arr[i] = bb.get() & 0xFF
            return bytes(arr)

        def _ensure_annex_b(raw: bytes) -> bytes:
            if not raw:
                return b""
            if raw.startswith(b"\x00\x00\x00\x01") or raw.startswith(b"\x00\x00\x01"):
                return raw
            # Convert length-prefixed AVCC/HVCC NAL units into 4-byte Annex-B start codes
            out = bytearray()
            pos = 0
            n = len(raw)
            while pos + 4 <= n:
                nal_len = int.from_bytes(raw[pos:pos + 4], "big")
                pos += 4
                if nal_len <= 0 or pos + nal_len > n:
                    # Not 4-byte length-prefixed; prepend start code directly
                    return b"\x00\x00\x00\x01" + raw
                out.extend(b"\x00\x00\x00\x01")
                out.extend(raw[pos:pos + nal_len])
                pos += nal_len
            return bytes(out) if out else (b"\x00\x00\x00\x01" + raw)

        tmp_out = f"{out_h264_path}.tmp"
        buf = ByteBuffer.allocate(512 * 1024)
        frames = 0
        with open(tmp_out, "wb") as f_out:
            for csd_key in ("csd-0", "csd-1"):
                if v_format.containsKey(csd_key):
                    csd_bytes = _read_bb(v_format.getByteBuffer(csd_key))
                    if csd_bytes:
                        f_out.write(_ensure_annex_b(csd_bytes))
            while True:
                buf.clear()
                sample_size = extractor.readSampleData(buf, 0)
                if sample_size < 0:
                    break
                arr = bytearray(sample_size)
                buf.rewind()
                for i in range(sample_size):
                    arr[i] = buf.get() & 0xFF
                f_out.write(_ensure_annex_b(bytes(arr)))
                frames += 1
                extractor.advance()

        if frames == 0 or not os.path.exists(tmp_out) or os.path.getsize(tmp_out) == 0:
            return False, "Video track yielded zero H.264 frames."
        os.replace(tmp_out, out_h264_path)
        return True, out_h264_path
    except Exception as e:
        return False, f"Android H.264 extract error: {e}"
    finally:
        try:
            extractor.release()
        except Exception:
            pass


def _android_extract_wav_from_media(media_path: str, out_wav_path: str) -> tuple[bool, str]:
    """Decode Android-supported audio (.m4a/.aac/.mp4) into 16kHz mono PCM WAV via MediaCodec."""
    try:
        from jnius import autoclass
        MediaExtractor = autoclass("android.media.MediaExtractor")
        MediaCodec = autoclass("android.media.MediaCodec")
        BufferInfo = autoclass("android.media.MediaCodec$BufferInfo")
        ByteBuffer = autoclass("java.nio.ByteBuffer")
    except Exception as e:
        return False, f"Android MediaCodec unavailable: {e}"

    extractor = MediaExtractor()
    codec = None
    try:
        extractor.setDataSource(media_path)
        a_track = -1
        a_format = None
        mime = ""
        for idx in range(extractor.getTrackCount()):
            fmt = extractor.getTrackFormat(idx)
            m = str(fmt.getString("mime") or "")
            if m.startswith("audio/"):
                a_track = idx
                a_format = fmt
                mime = m
                break
        if a_track < 0 or a_format is None:
            return False, "No audio track found."

        extractor.selectTrack(a_track)
        sample_rate = int(a_format.getInteger("sample-rate")) if a_format.containsKey("sample-rate") else 44100
        channels = int(a_format.getInteger("channel-count")) if a_format.containsKey("channel-count") else 2

        codec = MediaCodec.createDecoderByType(mime)
        codec.configure(a_format, None, None, 0)
        codec.start()

        info = BufferInfo()
        saw_input_eof = False
        saw_output_eof = False
        raw_pcm = bytearray()

        while not saw_output_eof:
            if not saw_input_eof:
                in_idx = codec.dequeueInputBuffer(5000)
                if in_idx >= 0:
                    in_buf = codec.getInputBuffer(in_idx)
                    sz = extractor.readSampleData(in_buf, 0)
                    if sz < 0:
                        codec.queueInputBuffer(in_idx, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        saw_input_eof = True
                    else:
                        pts = extractor.getSampleTime()
                        codec.queueInputBuffer(in_idx, 0, sz, pts, 0)
                        extractor.advance()

            out_idx = codec.dequeueOutputBuffer(info, 5000)
            if out_idx >= 0:
                out_buf = codec.getOutputBuffer(out_idx)
                if out_buf is not None and info.size > 0:
                    out_buf.position(info.offset)
                    out_buf.limit(info.offset + info.size)
                    chunk = bytearray(info.size)
                    for i in range(info.size):
                        chunk[i] = out_buf.get() & 0xFF
                    raw_pcm.extend(chunk)
                codec.releaseOutputBuffer(out_idx, False)
                if (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0:
                    saw_output_eof = True

        if not raw_pcm:
            return False, "Audio decoder produced no PCM output."

        tmp_wav = f"{out_wav_path}.tmp"
        with wave.open(tmp_wav, "wb") as wf:
            wf.setnchannels(max(1, channels))
            wf.setsampwidth(2)
            wf.setframerate(max(8000, sample_rate))
            wf.writeframes(bytes(raw_pcm))
        os.replace(tmp_wav, out_wav_path)
        return True, out_wav_path
    except Exception as e:
        return False, f"Android audio decode error: {e}"
    finally:
        if codec is not None:
            try:
                codec.stop()
                codec.release()
            except Exception:
                pass
        try:
            extractor.release()
        except Exception:
            pass


def prepare_audio_for_call(path: str) -> tuple[bool, str]:
    """Return a playable MP3/WAV/Opus file path for Go meowcaller, converting if necessary."""
    if not path or not os.path.isfile(path):
        return False, f"Audio file not found: {path}"

    if _is_native_go_audio(path):
        return True, path

    base = os.path.splitext(os.path.basename(path))[0]
    out_wav = os.path.join(call_cache_dir(), f"{base}_conv.wav")
    if os.path.exists(out_wav) and os.path.getmtime(out_wav) >= os.path.getmtime(path):
        return True, out_wav

    ff = shutil.which("ffmpeg")
    if ff:
        try:
            subprocess.run(
                [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", out_wav],
                check=True, timeout=60,
            )
            return True, out_wav
        except Exception as e:
            log.warning("ffmpeg audio convert failed: %s", e)

    ok, res = _android_extract_wav_from_media(path, out_wav)
    if ok:
        return True, res
    return False, f"Unsupported format ({os.path.basename(path)}). Use .mp3, .wav, or .ogg ({res})"


def prepare_video_for_call(path: str, fps: int = 15) -> tuple[bool, str, Optional[str], str]:
    """Prepare a video file into (ok, h264_path, optional_audio_path, status_msg)."""
    if not path or not os.path.isfile(path):
        return False, "", None, f"Video file not found: {path}"

    ext = os.path.splitext(path)[1].lower()
    if ext == ".h264":
        return True, path, None, "Raw H.264 stream ready"

    base = os.path.splitext(os.path.basename(path))[0]
    cache_dir = call_cache_dir()
    out_h264 = os.path.join(cache_dir, f"{base}.h264")
    out_audio = os.path.join(cache_dir, f"{base}_vtrack.mp3")
    out_wav = os.path.join(cache_dir, f"{base}_vtrack.wav")

    src_mtime = os.path.getmtime(path)
    if os.path.exists(out_h264) and os.path.getmtime(out_h264) >= src_mtime:
        aud = out_audio if os.path.exists(out_audio) else (out_wav if os.path.exists(out_wav) else None)
        return True, out_h264, aud, "Using cached H.264 + audio assets"

    ff = shutil.which("ffmpeg")
    if ff:
        keyint = str(max(1, int(fps)))
        try:
            subprocess.run(
                [
                    ff, "-y", "-hide_banner", "-loglevel", "error", "-t", "600", "-i", path,
                    "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                    "-profile:v", "main", "-level", "3.1", "-pix_fmt", "yuv420p", "-r", keyint,
                    "-vf", "scale=-2:480", "-g", keyint, "-keyint_min", keyint, "-sc_threshold", "0",
                    "-x264-params", "repeat-headers=1:bframes=0:open-gop=0:sliced-threads=0:slices=1",
                    "-b:v", "1200k", "-maxrate", "1800k", "-bufsize", "3600k", "-f", "h264", out_h264,
                    "-map", "0:a:0?", "-vn", "-ac", "1", "-ar", "16000", "-codec:a", "libmp3lame", "-q:a", "2", out_audio,
                ],
                check=True, timeout=180,
            )
            aud = out_audio if os.path.exists(out_audio) else None
            return True, out_h264, aud, "Transcoded video via ffmpeg"
        except Exception as e:
            log.warning("ffmpeg video transcode failed: %s", e)

    ok_v, msg_v = _android_extract_h264_from_mp4(path, out_h264)
    if not ok_v:
        return False, "", None, f"Could not extract H.264 video: {msg_v}"

    ok_a, _ = _android_extract_wav_from_media(path, out_wav)
    return True, out_h264, (out_wav if ok_a else None), "Extracted H.264 stream on-device"
