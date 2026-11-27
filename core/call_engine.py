"""High-level VoIP & Video Call controller bridging Go meowcaller FFI with Python plugins and UI."""
import asyncio
import ctypes
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import call_media

log = logging.getLogger("wa.calls")

_INSTANCES: dict[str, "CallEngine"] = {}
_GLOBAL_CB_REF = None


@dataclass
class CallEvent:
    event: str            # incoming | ready | peer_accept | state | end | audio_finish | video_state | mute_state | ...
    call_id: str
    peer: str = ""
    peer_number: str = ""
    direction: str = "in" # in | out
    is_video: bool = False
    phase: str = ""       # ringing | calling | connecting | active | ended
    reason: str = ""
    muted: bool = False
    video_active: bool = False
    video_upgrade: bool = False
    orientation: int = 0
    path: str = ""
    error: str = ""
    ts: int = 0
    raw: dict = None


def _clean_peer_number(peer: str) -> str:
    return (peer or "").split("@")[0].split(":")[0]


def _iter_annexb_access_units(read_chunk_fn):
    """Yield complete H.264 Annex-B Access Units from a chunked byte stream.

    Ensures SPS (NAL type 7) and PPS (NAL type 8) are prepended to every IDR (NAL type 5)
    when observed in the stream, handling 3-byte and 4-byte start codes across chunk splits.
    """
    buf = bytearray()
    sps: Optional[bytes] = None
    pps: Optional[bytes] = None
    pending: list[bytes] = []
    eof = False

    def _has_param(items: list[bytes], nalu_type: int) -> bool:
        for item in items:
            if item and (item[0] & 0x1F) == nalu_type:
                return True
        return False

    def _flush_nal(nal: bytes) -> Optional[bytes]:
        nonlocal sps, pps
        if not nal:
            return None
        ntype = nal[0] & 0x1F
        if ntype == 7:
            sps = bytes(nal)
        elif ntype == 8:
            pps = bytes(nal)
        pending.append(bytes(nal))
        if ntype in (1, 5):
            if ntype == 5:
                prefix: list[bytes] = []
                if sps and not _has_param(pending, 7):
                    prefix.append(sps)
                if pps and not _has_param(pending, 8):
                    prefix.append(pps)
                if prefix:
                    pending[:0] = prefix
            au = b"".join(b"\x00\x00\x00\x01" + n for n in pending)
            pending.clear()
            return au
        return None

    in_nal = False
    while not eof:
        chunk = read_chunk_fn()
        if not chunk:
            eof = True
        else:
            buf.extend(chunk)

        pos = 0
        limit = len(buf) if eof else max(0, len(buf) - 4)
        while pos < limit:
            idx = buf.find(b"\x00\x00\x01", pos)
            if idx < 0 or (not eof and idx > len(buf) - 4):
                break
            sc_start = idx - 1 if (idx > pos and buf[idx - 1] == 0) else idx
            if in_nal and sc_start > pos:
                au = _flush_nal(bytes(buf[pos:sc_start]))
                if au:
                    yield au
            in_nal = True
            pos = idx + 3

        if pos > 0:
            del buf[:pos]
        if eof:
            if in_nal and len(buf) > 0:
                au = _flush_nal(bytes(buf))
                if au:
                    yield au
            if pending and any((n[0] & 0x1F) in (1, 5) for n in pending if n):
                yield b"".join(b"\x00\x00\x00\x01" + n for n in pending)
            break


def _on_go_call_event(uuid_ptr: int, payload_ptr: int):
    try:
        uuid_str = ctypes.string_at(uuid_ptr).decode("utf-8", "ignore") if uuid_ptr else ""
        raw_str = ctypes.string_at(payload_ptr).decode("utf-8", "ignore") if payload_ptr else ""
        if not raw_str:
            return
        data = json.loads(raw_str)
    except Exception:
        log.debug("failed decoding Go call callback", exc_info=True)
        return

    inst = _INSTANCES.get(uuid_str)
    if not inst or not inst._loop:
        return
    try:
        asyncio.run_coroutine_threadsafe(inst._handle_go_event(data), inst._loop)
    except Exception:
        log.debug("failed scheduling Go call callback", exc_info=True)


class CallEngine:
    """Manages active calls, audio/video streaming, recordings, and persistent SQLite call log."""

    def __init__(self, core, private_dir: str):
        self.core = core
        self.private_dir = private_dir
        self.db_path = os.path.join(private_dir, "calls.sqlite3")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._uuid_bytes: Optional[bytes] = None
        self._uuid_str: str = ""
        self._active: dict[str, dict] = {}
        self._gocode = None
        self._resolve_gocode()
        self._init_db()
        try:
            call_media.ensure_builtin_tracks()
        except Exception:
            pass

    def _resolve_gocode(self):
        if self._gocode is not None:
            return self._gocode
        try:
            from neonize._binder import gocode
            self._gocode = gocode
        except Exception:
            self._gocode = None
        return self._gocode

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            with conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS call_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        call_id TEXT UNIQUE,
                        peer TEXT,
                        peer_number TEXT,
                        direction TEXT,
                        is_video INTEGER,
                        status TEXT,
                        reason TEXT,
                        track_played TEXT,
                        started_at INTEGER,
                        ended_at INTEGER
                    );
                """)
            conn.close()
        except Exception as e:
            log.warning("calls DB init error: %s", e)

    def _upsert_call_db(self, call_id: str, **fields):
        if not call_id:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            with conn:
                cur = conn.execute("SELECT call_id FROM call_log WHERE call_id = ?", (call_id,))
                exists = cur.fetchone() is not None
                if not exists:
                    conn.execute(
                        """
                        INSERT INTO call_log (
                            call_id, peer, peer_number, direction, is_video, status, reason, track_played, started_at, ended_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            call_id,
                            fields.get("peer", ""),
                            fields.get("peer_number", ""),
                            fields.get("direction", "in"),
                            1 if fields.get("is_video") else 0,
                            fields.get("status", "ringing"),
                            fields.get("reason", ""),
                            fields.get("track_played", ""),
                            int(fields.get("started_at", time.time())),
                            int(fields.get("ended_at", 0)),
                        ),
                    )
                else:
                    cols = []
                    vals = []
                    for k, v in fields.items():
                        if k == "is_video":
                            v = 1 if v else 0
                        cols.append(f"{k} = ?")
                        vals.append(v)
                    if cols:
                        vals.append(call_id)
                        conn.execute(f"UPDATE call_log SET {', '.join(cols)} WHERE call_id = ?", vals)
            conn.close()
        except Exception as e:
            log.debug("call DB upsert error: %s", e)

    def history(self, limit: int = 25) -> list[dict]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT call_id, peer, peer_number, direction, is_video, status, reason, track_played, started_at, ended_at "
                "FROM call_log ORDER BY started_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def clear_history(self) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            with conn:
                conn.execute("DELETE FROM call_log;")
            conn.close()
            return True
        except Exception:
            return False

    def attach_client(self, client, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Bind callback & UUID once NewAClient is constructed."""
        global _GLOBAL_CB_REF
        self._loop = loop or asyncio.get_running_loop()
        uuid_val = getattr(client, "uuid", b"")
        if isinstance(uuid_val, str):
            uuid_val = uuid_val.encode("utf-8")
        self._uuid_bytes = uuid_val
        self._uuid_str = uuid_val.decode("utf-8", "ignore")
        if self._uuid_str:
            _INSTANCES[self._uuid_str] = self

        try:
            from neonize._binder import func_string, gocode
            self._gocode = gocode
            if _GLOBAL_CB_REF is None and hasattr(gocode, "CallRegisterCallback"):
                _GLOBAL_CB_REF = func_string(_on_go_call_event)
                gocode.CallRegisterCallback(_GLOBAL_CB_REF)
        except Exception as e:
            log.warning("Could not register Go VoIP callback: %s", e)

    def is_supported(self) -> bool:
        g = self._resolve_gocode()
        return bool(g and hasattr(g, "CallPlace") and hasattr(g, "CallAnswer"))

    async def _call_ffi(self, fn_name: str, *args) -> dict:
        g = self._resolve_gocode()
        if not g or not hasattr(g, fn_name):
            return {"ok": False, "error": f"VoIP binary does not export {fn_name} (rebuild APK/SO required)"}
        if not self._uuid_bytes:
            return {"ok": False, "error": "WhatsApp client UUID not initialized"}

        fn = getattr(g, fn_name)
        encoded = [self._uuid_bytes]
        for a in args:
            if isinstance(a, str):
                encoded.append(a.encode("utf-8"))
            else:
                encoded.append(a)
        try:
            raw = await asyncio.to_thread(fn, *encoded)
            txt = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
            if not txt:
                return {"ok": False, "error": "empty response from Go VoIP bridge"}
            return json.loads(txt)
        except Exception as e:
            log.exception("Call FFI %s failed", fn_name)
            return {"ok": False, "error": str(e)}

    async def _handle_go_event(self, data: dict):
        ev_name = str(data.get("event") or "")
        call_id = str(data.get("call_id") or "")
        if ev_name == "log":
            msg_txt = str(data.get("msg") or "")
            if msg_txt:
                log.info("[VoIP] %s", msg_txt)
            return

        peer = str(data.get("peer") or "")
        ev = CallEvent(
            event=ev_name,
            call_id=call_id,
            peer=peer,
            peer_number=_clean_peer_number(peer),
            direction=str(data.get("direction") or "in"),
            is_video=bool(data.get("is_video")),
            phase=str(data.get("phase") or ""),
            reason=str(data.get("reason") or ""),
            muted=bool(data.get("muted")),
            video_active=bool(data.get("video_active")),
            video_upgrade=bool(data.get("video_upgrade")),
            orientation=int(data.get("orientation") or 0),
            path=str(data.get("path") or ""),
            error=str(data.get("error") or ""),
            ts=int(data.get("ts") or time.time()),
            raw=data,
        )

        # Maintain local state & persistent DB
        if call_id:
            entry = self._active.setdefault(call_id, {
                "call_id": call_id,
                "peer": ev.peer,
                "peer_number": ev.peer_number,
                "direction": ev.direction,
                "is_video": ev.is_video,
                "phase": ev.phase or "ringing",
                "audio_state": "idle",
                "audio_path": "",
                "started_at": ev.ts,
            })
            if ev.peer:
                entry["peer"] = ev.peer
                entry["peer_number"] = ev.peer_number
            if ev.phase:
                entry["phase"] = ev.phase
            if "is_video" in data:
                entry["is_video"] = ev.is_video

            if ev_name == "incoming":
                self._upsert_call_db(
                    call_id, peer=ev.peer, peer_number=ev.peer_number,
                    direction="in", is_video=ev.is_video, status="ringing", started_at=ev.ts
                )
                self.core.emit("/log", f"📞 Incoming {'video' if ev.is_video else 'voice'} call from {ev.peer_number} ({call_id[:8]})")
            elif ev_name == "ready":
                entry["phase"] = "active"
                self._upsert_call_db(call_id, status="active")
                self.core.emit("/log", f"🎧 Call active with {ev.peer_number} ({call_id[:8]})")
            elif ev_name == "audio_finish":
                entry["audio_state"] = "idle"
            elif ev_name == "end":
                entry["phase"] = "ended"
                status = "ended"
                if ev.reason in ("rejected", "declined"):
                    status = "rejected"
                elif ev.reason in ("timeout", "missed"):
                    status = "missed"
                self._upsert_call_db(call_id, status=status, reason=ev.reason, ended_at=ev.ts)
                self._active.pop(call_id, None)
                self.core.emit("/log", f"📴 Call ended ({ev.peer_number}): {ev.reason or 'hangup'}")

        # Notify UI via OSC
        try:
            self.core.emit("/call_event", json.dumps({
                "event": ev.event,
                "call_id": ev.call_id,
                "peer": ev.peer,
                "peer_number": ev.peer_number,
                "direction": ev.direction,
                "is_video": ev.is_video,
                "phase": ev.phase,
                "reason": ev.reason,
                "path": ev.path,
                "error": ev.error,
                "active_calls": list(self._active.values()),
            }))
        except Exception:
            pass

        # Dispatch to plugins (only when plugin has on_call)
        try:
            self.core.plugins.dispatch_call(ev)
        except Exception as e:
            log.debug("plugin call dispatch failed: %s", e)

    # ---- High-level async VoIP actions
    async def place_call(self, target: str, is_video: bool = False) -> dict:
        if hasattr(self.core, "wait_until_ready"):
            ready = await self.core.wait_until_ready(timeout=6.0)
            if not ready:
                log.warning("Placing call before session sync settled")
        res = await self._call_ffi("CallPlace", target, 1 if is_video else 0)
        if res.get("ok"):
            cid = str(res.get("call_id") or "")
            peer = str(res.get("peer") or target)
            pnum = _clean_peer_number(peer)
            self._active[cid] = {
                "call_id": cid,
                "peer": peer,
                "peer_number": pnum,
                "direction": "out",
                "is_video": bool(is_video),
                "phase": str(res.get("phase") or "calling"),
                "audio_state": "idle",
                "audio_path": "",
                "started_at": int(time.time()),
            }
            self._upsert_call_db(
                cid, peer=peer, peer_number=pnum,
                direction="out", is_video=is_video, status="calling", started_at=int(time.time())
            )
            self.core.emit("/log", f"📲 Placing {'video' if is_video else 'voice'} call to {pnum}...")
            self.core.emit("/call_event", json.dumps({
                "event": "outgoing",
                "call_id": cid,
                "peer": peer,
                "peer_number": pnum,
                "direction": "out",
                "is_video": bool(is_video),
                "phase": "calling",
                "active_calls": list(self._active.values()),
            }))
        return res

    async def answer(self, call_id: str) -> dict:
        res = await self._call_ffi("CallAnswer", call_id)
        if res.get("ok"):
            self._upsert_call_db(call_id, status="answered")
        return res

    async def decline(self, call_id: str, caller_jid=None) -> dict:
        res = await self._call_ffi("CallDecline", call_id)
        if not res.get("ok") and caller_jid is not None and self.core.client:
            try:
                await self.core.client.reject_call(caller_jid, call_id)
                res = {"ok": True, "call_id": call_id}
            except Exception as e:
                res = {"ok": False, "error": str(e)}
        if res.get("ok"):
            self._upsert_call_db(call_id, status="rejected", reason="rejected", ended_at=int(time.time()))
        return res

    async def hangup(self, call_id: str) -> dict:
        res = await self._call_ffi("CallHangup", call_id)
        if res.get("ok"):
            self._upsert_call_db(call_id, status="ended", reason="hangup", ended_at=int(time.time()))
        return res

    async def play_audio(self, call_id: str, path_or_query: str = "", loop: bool = False) -> dict:
        resolved = await asyncio.to_thread(call_media.resolve_track_path, path_or_query, "audio")
        if not resolved:
            return {"ok": False, "error": f"Audio track not found: {path_or_query or '(empty)'}"}
        ok_prep, prep_path_or_err = await asyncio.to_thread(call_media.prepare_audio_for_call, resolved)
        if not ok_prep:
            return {"ok": False, "error": prep_path_or_err}

        res = await self._call_ffi("CallPlayAudio", call_id, prep_path_or_err, 1 if loop else 0)
        if res.get("ok"):
            fname = os.path.basename(resolved)
            if call_id in self._active:
                self._active[call_id]["audio_state"] = "playing"
                self._active[call_id]["audio_path"] = fname
            self._upsert_call_db(call_id, track_played=fname)
            self.core.emit("/log", f"🎵 Playing in call {call_id[:8]}: {fname} (loop={'ON' if loop else 'OFF'})")
        return res

    async def play_pcm_stream(self, call_id: str, label: str = "Live Audio Stream", prebuf_frames: int = 4) -> dict:
        """Open a live 16kHz mono s16le PCM loopback socket wired into meowcaller.Player."""
        res = await self._call_ffi("CallPlayPCMStream", call_id, label, int(prebuf_frames))
        if res.get("ok"):
            if call_id in self._active:
                self._active[call_id]["audio_state"] = "playing"
                self._active[call_id]["audio_path"] = label
                self._active[call_id]["stream_gen"] = res.get("gen", 0)
            self._upsert_call_db(call_id, track_played=label)
            self.core.emit("/log", f"📡 Live PCMStream active in call {call_id[:8]}: {label} (port={res.get('port')})")
        return res

    @staticmethod
    def _get_java_stream_state(port: int) -> str:
        try:
            from jnius import autoclass
            Transcoder = autoclass("org.sanatani.waapp.WaMediaTranscoder")
            return str(Transcoder.getStreamState(int(port)) or "")
        except Exception:
            return ""

    @staticmethod
    def _stream_url_worker_sync(
        url: str,
        headers: Optional[dict],
        content_length: int,
        port: int,
        max_seconds: int,
        started_ev=None,
    ) -> str:
        """Blocking worker: decode remote audio URL to live 16kHz s16le PCM over 127.0.0.1:port."""
        headers_json = json.dumps(headers or {})
        try:
            from jnius import autoclass
            Transcoder = autoclass("org.sanatani.waapp.WaMediaTranscoder")
            return str(
                Transcoder.streamUrlToPcmSocket(
                    str(url),
                    headers_json,
                    int(content_length or 0),
                    "127.0.0.1",
                    int(port),
                    int(max_seconds or 0),
                )
                or ""
            )
        except Exception as android_exc:
            ff = shutil.which("ffmpeg")
            if not ff:
                return f"No live HTTP audio decoder available ({android_exc})"

        # Desktop/Termux fallback: FFmpeg decodes URL chunks live -> s16le PCM pipe -> TCP socket
        is_http = str(url or "").lower().startswith(("http://", "https://"))
        hdr_lines = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items() if v)
        cmd = [ff, "-hide_banner", "-loglevel", "error"]
        if is_http:
            if hdr_lines:
                cmd.extend(["-headers", hdr_lines])
            cmd.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "2"])
        if max_seconds and max_seconds > 0:
            cmd.extend(["-t", str(int(max_seconds))])
        cmd.extend(["-i", url, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"])

        proc = None
        sock = None
        total_sent = 0
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            first_chunk = proc.stdout.read(3840) if proc.stdout else b""
            if not first_chunk:
                err_txt = (proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else "").strip()
                if "403" in err_txt or "410" in err_txt:
                    return f"HTTP_REFUSED:{403 if '403' in err_txt else 410}"
                return err_txt or "ffmpeg produced zero PCM bytes"

            sock = socket.create_connection(("127.0.0.1", int(port)), timeout=5.0)
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.sendall(first_chunk)
            total_sent += len(first_chunk)
            if started_ev is not None:
                started_ev.set()

            while True:
                buf = proc.stdout.read(8192)
                if not buf:
                    break
                sock.sendall(buf)
                total_sent += len(buf)
            return ""
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Go closed socket (stop/skip/hangup) cleanly
            return "" if total_sent > 0 else "PCM socket closed"
        except Exception as e:
            return f"ffmpeg stream failed: {e}"
        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
            if proc is not None:
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    @staticmethod
    def _clear_java_stream_state(port: int) -> None:
        try:
            from jnius import autoclass
            Transcoder = autoclass("org.sanatani.waapp.WaMediaTranscoder")
            Transcoder.clearStreamState(int(port))
        except Exception:
            pass

    async def play_url_stream(
        self,
        call_id: str,
        url: str,
        headers: Optional[dict] = None,
        content_length: int = 0,
        label: str = "Live Stream",
        max_seconds: int = 3600,
        prebuf_frames: int = 4,
    ) -> dict:
        """Start true real-time zero-download audio streaming into a call from a direct media URL."""
        res = await self.play_pcm_stream(call_id, label=label, prebuf_frames=prebuf_frames)
        if not res.get("ok"):
            return res

        port = int(res.get("port") or 0)
        gen = res.get("gen", 0)
        if port <= 0:
            return {"ok": False, "error": "Invalid PCM stream port from Go bridge"}

        import threading as _threading
        self._clear_java_stream_state(port)
        started_ev = _threading.Event()
        feeder_task = asyncio.create_task(
            asyncio.to_thread(
                self._stream_url_worker_sync,
                url,
                headers,
                int(content_length or 0),
                port,
                int(max_seconds or 0),
                started_ev,
            )
        )

        async def _watch_late_failure():
            try:
                err = await feeder_task
                if err:
                    cur = self._active.get(call_id)
                    if cur and cur.get("stream_gen") == gen:
                        log.warning("Live stream ended with error (%s): %s", label, err)
                        self.core.emit("/log", f"⚠️ Stream error ({label}): {err}")
                        await self._call_ffi("CallStopAudio", call_id)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.debug("stream watcher error: %s", e)

        # Wait up to 12s ONLY until the very first PCM frame flows (typically < 1s) or early error occurs
        deadline = time.monotonic() + 12.0
        while not feeder_task.done() and time.monotonic() < deadline:
            if started_ev.is_set():
                break
            jst = self._get_java_stream_state(port)
            if jst in ("streaming", "done"):
                break
            await asyncio.sleep(0.05)

        if feeder_task.done():
            err = ""
            try:
                err = feeder_task.result() or ""
            except Exception as exc:
                err = str(exc)
            if err:
                cur = self._active.get(call_id)
                if cur and cur.get("stream_gen") == gen:
                    await self._call_ffi("CallStopAudio", call_id)
                return {
                    "ok": False,
                    "error": err,
                    "refused": err.startswith("HTTP_REFUSED:"),
                }
        else:
            asyncio.create_task(_watch_late_failure())

        return {"ok": True, "call_id": call_id, "port": port, "label": label, "gen": gen}

    @staticmethod
    def _stream_av_worker_sync(
        video_url: str,
        audio_url: str,
        headers: Optional[dict],
        v_len: int,
        a_len: int,
        video_port: int,
        audio_port: int,
        max_dim: int,
        fps: int,
        bitrate: int,
        max_seconds: int,
        started_ev=None,
    ) -> str:
        """Blocking worker: stream remote video+audio URLs into Go's live video+PCM sockets."""
        headers_json = json.dumps(headers or {})
        try:
            from jnius import autoclass
            Transcoder = autoclass("org.sanatani.waapp.WaMediaTranscoder")
            return str(
                Transcoder.streamAvUrlsToSockets(
                    str(video_url),
                    str(audio_url or ""),
                    headers_json,
                    int(v_len or 0),
                    int(a_len or 0),
                    int(video_port),
                    int(audio_port),
                    int(max_dim or 480),
                    int(fps or 15),
                    int(bitrate or 650000),
                    int(max_seconds or 0),
                )
                or ""
            )
        except Exception as android_exc:
            ff = shutil.which("ffmpeg")
            if not ff:
                return f"No live HTTP AV decoder available ({android_exc})"

        import threading as _threading

        eff_audio_url = audio_url or video_url
        audio_started = _threading.Event()
        audio_err_holder = [""]

        def _audio_worker():
            audio_err_holder[0] = CallEngine._stream_url_worker_sync(
                eff_audio_url,
                headers,
                int(a_len or 0),
                int(audio_port),
                int(max_seconds or 0),
                started_ev=audio_started,
            )

        a_thread = _threading.Thread(target=_audio_worker, name="WaFFmpegAudioStream", daemon=True)
        a_thread.start()

        fps_eff = max(1, min(30, int(fps or 15)))
        dim_eff = max(160, int(max_dim or 480))
        br_eff = max(100000, int(bitrate or 650000))
        is_http = str(video_url or "").lower().startswith(("http://", "https://"))
        hdr_lines = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items() if v)
        vf = (
            f"scale='min({dim_eff},iw)':'min({dim_eff},ih)':force_original_aspect_ratio=decrease,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,fps={fps_eff}"
        )
        v_cmd = [ff, "-hide_banner", "-loglevel", "error"]
        if is_http:
            if hdr_lines:
                v_cmd.extend(["-headers", hdr_lines])
            v_cmd.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "2"])
        if max_seconds and max_seconds > 0:
            v_cmd.extend(["-t", str(int(max_seconds))])
        v_cmd.extend([
            "-i", video_url,
            "-an", "-vf", vf,
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.1",
            "-pix_fmt", "yuv420p", "-bf", "0",
            "-g", str(fps_eff), "-keyint_min", str(fps_eff),
            "-b:v", str(br_eff), "-maxrate", str(int(br_eff * 1.3)), "-bufsize", str(br_eff),
            "-preset", "ultrafast", "-tune", "zerolatency",
            "-f", "h264", "pipe:1",
        ])

        proc = None
        v_sock = None
        sent_aus = 0
        try:
            proc = subprocess.Popen(v_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            au_iter = _iter_annexb_access_units(lambda: proc.stdout.read(16384) if proc.stdout else b"")
            first_au = next(au_iter, b"")
            if not first_au:
                err_txt = (proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else "").strip()
                if "403" in err_txt or "410" in err_txt:
                    return f"HTTP_REFUSED:{403 if '403' in err_txt else 410}"
                if audio_err_holder[0]:
                    return audio_err_holder[0]
                return err_txt or "ffmpeg produced zero H.264 access units"

            v_sock = socket.create_connection(("127.0.0.1", int(video_port)), timeout=5.0)
            v_sock.settimeout(None)
            v_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            v_sock.sendall(len(first_au).to_bytes(4, "big") + first_au)
            sent_aus += 1
            if started_ev is not None:
                started_ev.set()

            for au in au_iter:
                if audio_err_holder[0]:
                    break
                if au:
                    v_sock.sendall(len(au).to_bytes(4, "big") + au)
                    sent_aus += 1

            a_thread.join(timeout=5.0)
            return audio_err_holder[0] or ""
        except (BrokenPipeError, ConnectionResetError, OSError):
            a_thread.join(timeout=1.5)
            if audio_err_holder[0]:
                return audio_err_holder[0]
            return "" if sent_aus > 0 else "AV video socket closed"
        except Exception as e:
            return f"ffmpeg AV stream failed: {e}"
        finally:
            try:
                if v_sock is not None:
                    v_sock.close()
            except Exception:
                pass
            if proc is not None:
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    async def play_av_stream(
        self,
        call_id: str,
        video_url: str,
        audio_url: str = "",
        headers: Optional[dict] = None,
        v_len: int = 0,
        a_len: int = 0,
        label: str = "Live Video Stream",
        fps: int = 15,
        max_dim: int = 480,
        bitrate: int = 650000,
        max_seconds: int = 600,
        prebuf_frames: int = 8,
    ) -> dict:
        """Start true real-time zero-download video+audio streaming into an active call."""
        res = await self._call_ffi("CallPlayAVStream", call_id, label, int(fps), int(prebuf_frames))
        if not res.get("ok"):
            return res

        audio_port = int(res.get("audio_port") or 0)
        video_port = int(res.get("video_port") or 0)
        gen = res.get("gen", 0)
        fps_eff = int(res.get("fps") or fps or 15)
        if audio_port <= 0 or video_port <= 0:
            await self._call_ffi("CallStopVideoStream", call_id)
            await self._call_ffi("CallStopAudio", call_id)
            return {"ok": False, "error": "Invalid AV stream ports from Go bridge"}

        if call_id in self._active:
            self._active[call_id]["audio_state"] = "playing"
            self._active[call_id]["audio_path"] = label
            self._active[call_id]["stream_gen"] = gen
        self._upsert_call_db(call_id, track_played=label)
        self.core.emit(
            "/log",
            f"🎬 Live AVStream active in call {call_id[:8]}: {label} (v={video_port}, a={audio_port})",
        )

        import threading as _threading
        self._clear_java_stream_state(audio_port)
        self._clear_java_stream_state(video_port)
        started_ev = _threading.Event()
        feeder_task = asyncio.create_task(
            asyncio.to_thread(
                self._stream_av_worker_sync,
                video_url,
                audio_url,
                headers,
                int(v_len or 0),
                int(a_len or 0),
                video_port,
                audio_port,
                int(max_dim or 480),
                fps_eff,
                int(bitrate or 650000),
                int(max_seconds or 0),
                started_ev,
            )
        )

        async def _watch_late_failure():
            try:
                err = await feeder_task
                if err:
                    cur = self._active.get(call_id)
                    if cur and cur.get("stream_gen") == gen:
                        log.warning("Live AV stream ended with error (%s): %s", label, err)
                        self.core.emit("/log", f"⚠️ AV stream error ({label}): {err}")
                        await self._call_ffi("CallStopVideoStream", call_id)
                        await self._call_ffi("CallStopAudio", call_id)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.debug("AV stream watcher error: %s", e)

        deadline = time.monotonic() + 15.0
        while not feeder_task.done() and time.monotonic() < deadline:
            if started_ev.is_set():
                break
            jst_a = self._get_java_stream_state(audio_port)
            jst_v = self._get_java_stream_state(video_port)
            if jst_a in ("streaming", "done") or jst_v in ("streaming", "done"):
                break
            await asyncio.sleep(0.05)

        if feeder_task.done():
            err = ""
            try:
                err = feeder_task.result() or ""
            except Exception as exc:
                err = str(exc)
            if err:
                cur = self._active.get(call_id)
                if cur and cur.get("stream_gen") == gen:
                    await self._call_ffi("CallStopVideoStream", call_id)
                    await self._call_ffi("CallStopAudio", call_id)
                return {
                    "ok": False,
                    "error": err,
                    "refused": err.startswith("HTTP_REFUSED:"),
                }
        else:
            asyncio.create_task(_watch_late_failure())

        return {
            "ok": True,
            "call_id": call_id,
            "audio_port": audio_port,
            "video_port": video_port,
            "fps": fps_eff,
            "label": label,
            "gen": gen,
        }

    async def pause_audio(self, call_id: str) -> dict:
        res = await self._call_ffi("CallPauseAudio", call_id)
        if res.get("ok") and call_id in self._active:
            self._active[call_id]["audio_state"] = "paused"
        return res

    async def resume_audio(self, call_id: str) -> dict:
        res = await self._call_ffi("CallResumeAudio", call_id)
        if res.get("ok") and call_id in self._active:
            self._active[call_id]["audio_state"] = "playing"
        return res

    async def stop_audio(self, call_id: str) -> dict:
        res = await self._call_ffi("CallStopAudio", call_id)
        if res.get("ok") and call_id in self._active:
            self._active[call_id]["audio_state"] = "idle"
            self._active[call_id]["audio_path"] = ""
        return res

    async def play_video(self, call_id: str, path_or_query: str = "", fps: int = 15, loop: bool = True, with_audio: bool = True) -> dict:
        resolved = await asyncio.to_thread(call_media.resolve_track_path, path_or_query, "video")
        if not resolved:
            return {"ok": False, "error": f"Video file not found: {path_or_query or '(empty)'}"}
        ok_v, h264_path, aud_path, msg_v = await asyncio.to_thread(call_media.prepare_video_for_call, resolved, fps)
        if not ok_v:
            return {"ok": False, "error": msg_v}

        aud_arg = (aud_path or "") if with_audio else ""
        g = self._resolve_gocode()
        if g and hasattr(g, "CallPlayAV"):
            res = await self._call_ffi("CallPlayAV", call_id, h264_path, aud_arg, int(fps), 1 if loop else 0)
        else:
            res = await self._call_ffi("CallPlayVideo", call_id, h264_path, int(fps), 1 if loop else 0)
            if res.get("ok") and aud_arg:
                await self._call_ffi("CallPlayAudio", call_id, aud_arg, 1 if loop else 0)

        if res.get("ok"):
            fname = os.path.basename(resolved)
            if call_id in self._active and aud_arg:
                self._active[call_id]["audio_state"] = "playing"
                self._active[call_id]["audio_path"] = fname
            self._upsert_call_db(call_id, track_played=fname)
            self.core.emit("/log", f"🎬 Streaming video in call {call_id[:8]}: {fname} ({msg_v})")
        return res

    async def stop_video_stream(self, call_id: str) -> dict:
        return await self._call_ffi("CallStopVideoStream", call_id)

    async def start_video(self, call_id: str) -> dict:
        return await self._call_ffi("CallStartVideo", call_id)

    async def accept_video(self, call_id: str) -> dict:
        return await self._call_ffi("CallAcceptVideo", call_id)

    async def stop_video(self, call_id: str) -> dict:
        return await self._call_ffi("CallStopVideo", call_id)

    async def record_audio(self, call_id: str, wav_path: str = "auto") -> dict:
        target = wav_path
        if wav_path == "auto":
            ts = time.strftime("%Y%m%d_%H%M%S")
            target = os.path.join(call_media.call_recordings_dir(), f"call_{call_id[:8]}_{ts}.wav")
        return await self._call_ffi("CallRecordAudio", call_id, target)

    async def record_video(self, call_id: str, h264_path: str = "auto") -> dict:
        target = h264_path
        if h264_path == "auto":
            ts = time.strftime("%Y%m%d_%H%M%S")
            target = os.path.join(call_media.call_recordings_dir(), f"video_{call_id[:8]}_{ts}.h264")
        return await self._call_ffi("CallRecordVideo", call_id, target)

    async def send_reaction(self, call_id: str, emoji: str) -> dict:
        return await self._call_ffi("CallSendReaction", call_id, emoji)

    async def list_active(self) -> list[dict]:
        res = await self._call_ffi("CallListActive")
        if res.get("ok") and isinstance(res.get("calls"), list):
            return res["calls"]
        return list(self._active.values())

    def latest_active_call_id(self) -> Optional[str]:
        if not self._active:
            return None
        return list(self._active.keys())[-1]
