"""High-level VoIP & Video Call controller bridging Go meowcaller FFI with Python plugins and UI."""
import asyncio
import ctypes
import json
import logging
import os
import sqlite3
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

        res = await self._call_ffi("CallPlayVideo", call_id, h264_path, int(fps), 1 if loop else 0)
        if res.get("ok"):
            if with_audio and aud_path:
                await self._call_ffi("CallPlayAudio", call_id, aud_path, 1 if loop else 0)
            fname = os.path.basename(resolved)
            self._upsert_call_db(call_id, track_played=fname)
            self.core.emit("/log", f"🎬 Streaming video in call {call_id[:8]}: {fname}")
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
