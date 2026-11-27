"""Glue between neonize (async client), ClientDB, GhostMode, queue, supervisor & plugins."""
import asyncio
import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import core._shims
from common import qr_cache_path
from . import call_media
from .call_engine import CallEngine
from .client_db import ClientDB, normalize_jid
from .ghost import GhostConfig, GhostGuard
from .plugins import PluginManager
from .throttle import Job, OutboundQueue, PluginSupervisor

log = logging.getLogger("wa.core")

_MIME_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/3gpp": ".3gp",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "application/pdf": ".pdf",
}


def _norm_ts(ts: Any) -> int:
    """Normalize WhatsApp timestamps (seconds or milliseconds) to integer seconds."""
    try:
        val = int(ts or 0)
    except Exception:
        return 0
    if val > 10_000_000_000:
        val //= 1000
    return max(0, val)


def _jid_str(jid_obj: Any) -> str:
    """Convert a Neonize JID object or string JID into a normalized 'user@server' string."""
    if jid_obj is None:
        return ""
    if isinstance(jid_obj, str):
        return normalize_jid(jid_obj)
    user = getattr(jid_obj, "User", "") or ""
    server = getattr(jid_obj, "Server", "") or ""
    if not user and not server:
        return ""
    if user and server:
        return normalize_jid(f"{user}@{server}")
    return str(user or server)


def _has_field(obj: Any, field: str) -> bool:
    """Safely check protobuf HasField or attribute presence on mock objects."""
    if obj is None:
        return False
    hf = getattr(obj, "HasField", None)
    if callable(hf):
        try:
            return bool(hf(field))
        except Exception:
            return False
    return getattr(obj, field, None) is not None


def _unwrap_message(m: Any) -> tuple[Any, bool]:
    """Unwrap ephemeral / viewOnce / deviceSent / documentWithCaption / edited wrappers."""
    is_vo = False
    cur = m
    for _ in range(4):
        if cur is None:
            break
        if _has_field(cur, "viewOnceMessage"):
            cur = cur.viewOnceMessage.message
            is_vo = True
        elif _has_field(cur, "viewOnceMessageV2"):
            cur = cur.viewOnceMessageV2.message
            is_vo = True
        elif _has_field(cur, "viewOnceMessageV2Extension"):
            cur = cur.viewOnceMessageV2Extension.message
            is_vo = True
        elif _has_field(cur, "ephemeralMessage"):
            cur = cur.ephemeralMessage.message
        elif _has_field(cur, "documentWithCaptionMessage"):
            cur = cur.documentWithCaptionMessage.message
        elif _has_field(cur, "deviceSentMessage"):
            cur = cur.deviceSentMessage.message
        elif _has_field(cur, "editedMessage"):
            cur = cur.editedMessage.message
        else:
            break
    return cur, is_vo


def _extract_text_from_proto(m: Any) -> str:
    """Extract readable text/caption summary from a protobuf Message."""
    inner, _ = _unwrap_message(m)
    if inner is None:
        return ""
    conv = getattr(inner, "conversation", "")
    if conv:
        return str(conv)
    if _has_field(inner, "extendedTextMessage") and getattr(inner.extendedTextMessage, "text", ""):
        return str(inner.extendedTextMessage.text)
    if _has_field(inner, "imageMessage"):
        return str(getattr(inner.imageMessage, "caption", "") or "📷 [Photo]")
    if _has_field(inner, "videoMessage"):
        return str(getattr(inner.videoMessage, "caption", "") or "🎥 [Video]")
    if _has_field(inner, "audioMessage"):
        return "🎵 [Audio]"
    if _has_field(inner, "documentMessage"):
        cap = str(getattr(inner.documentMessage, "caption", "") or "")
        fn = str(getattr(inner.documentMessage, "fileName", "") or "")
        return cap or (f"📄 {fn}" if fn else "📄 [Document]")
    if _has_field(inner, "stickerMessage"):
        return "🎭 [Sticker]"
    return ""


def _ext_for_media(msg_type: str, mime: str, filename: str = "") -> str:
    if filename:
        _, fext = os.path.splitext(filename)
        if fext:
            return fext
    base_mime = (mime or "").split(";", 1)[0].strip().lower()
    if base_mime in _MIME_EXT_MAP:
        return _MIME_EXT_MAP[base_mime]
    return {
        "image": ".jpg",
        "video": ".mp4",
        "audio": ".ogg",
        "sticker": ".webp",
        "document": ".bin",
    }.get(msg_type, ".bin")


def _extract_message_payload(m: Any) -> Optional[dict[str, Any]]:
    """Extract normalized UI/DB fields from a protobuf Message (returns None for pure control frames)."""
    inner, is_vo = _unwrap_message(m)
    if inner is None:
        return None

    conv = getattr(inner, "conversation", "")
    if conv:
        txt = str(conv)
        return {
            "msg_type": "text",
            "body": txt,
            "preview": txt,
            "media_mime": "",
            "quoted_id": "",
            "is_view_once": is_vo,
            "media_pb": None,
            "filename": "",
        }

    if _has_field(inner, "extendedTextMessage"):
        et = inner.extendedTextMessage
        txt = str(getattr(et, "text", "") or "")
        qid = str(getattr(getattr(et, "contextInfo", None), "stanzaID", "") or "")
        return {
            "msg_type": "text",
            "body": txt,
            "preview": txt,
            "media_mime": "",
            "quoted_id": qid,
            "is_view_once": is_vo,
            "media_pb": None,
            "filename": "",
        }

    if _has_field(inner, "imageMessage"):
        im = inner.imageMessage
        cap = str(getattr(im, "caption", "") or "")
        vo = is_vo or bool(getattr(im, "viewOnce", False))
        mime = str(getattr(im, "mimetype", "") or "image/jpeg")
        qid = str(getattr(getattr(im, "contextInfo", None), "stanzaID", "") or "")
        label = "👁️ View-Once Photo" if vo else "📷 Photo"
        return {
            "msg_type": "image",
            "body": cap or label,
            "preview": f"📷 {cap}" if cap else label,
            "media_mime": mime,
            "quoted_id": qid,
            "is_view_once": vo,
            "media_pb": inner,
            "filename": "",
        }

    if _has_field(inner, "videoMessage") or _has_field(inner, "ptvMessage"):
        vm = inner.videoMessage if _has_field(inner, "videoMessage") else inner.ptvMessage
        cap = str(getattr(vm, "caption", "") or "")
        vo = is_vo or bool(getattr(vm, "viewOnce", False))
        mime = str(getattr(vm, "mimetype", "") or "video/mp4")
        qid = str(getattr(getattr(vm, "contextInfo", None), "stanzaID", "") or "")
        label = "👁️ View-Once Video" if vo else "🎥 Video"
        return {
            "msg_type": "video",
            "body": cap or label,
            "preview": f"🎥 {cap}" if cap else label,
            "media_mime": mime,
            "quoted_id": qid,
            "is_view_once": vo,
            "media_pb": inner,
            "filename": "",
        }

    if _has_field(inner, "audioMessage"):
        am = inner.audioMessage
        sec = int(getattr(am, "seconds", 0) or 0)
        vo = is_vo or bool(getattr(am, "viewOnce", False))
        mime = str(getattr(am, "mimetype", "") or "audio/ogg")
        qid = str(getattr(getattr(am, "contextInfo", None), "stanzaID", "") or "")
        label = (
            "👁️ View-Once Voice Note"
            if vo
            else (f"🎵 Voice Note ({sec}s)" if sec else "🎵 Voice Note")
        )
        return {
            "msg_type": "audio",
            "body": label,
            "preview": label,
            "media_mime": mime,
            "quoted_id": qid,
            "is_view_once": vo,
            "media_pb": inner,
            "filename": "",
        }

    if _has_field(inner, "documentMessage"):
        dm = inner.documentMessage
        fname = str(getattr(dm, "fileName", "") or "Document")
        cap = str(getattr(dm, "caption", "") or "")
        mime = str(getattr(dm, "mimetype", "") or "application/octet-stream")
        qid = str(getattr(getattr(dm, "contextInfo", None), "stanzaID", "") or "")
        return {
            "msg_type": "document",
            "body": f"📄 {fname}\n{cap}" if cap else f"📄 {fname}",
            "preview": f"📄 {fname}",
            "media_mime": mime,
            "quoted_id": qid,
            "is_view_once": is_vo,
            "media_pb": inner,
            "filename": fname,
        }

    if _has_field(inner, "stickerMessage"):
        sm = inner.stickerMessage
        mime = str(getattr(sm, "mimetype", "") or "image/webp")
        qid = str(getattr(getattr(sm, "contextInfo", None), "stanzaID", "") or "")
        return {
            "msg_type": "sticker",
            "body": "🎭 Sticker",
            "preview": "🎭 Sticker",
            "media_mime": mime,
            "quoted_id": qid,
            "is_view_once": is_vo,
            "media_pb": inner,
            "filename": "",
        }

    if _has_field(inner, "contactMessage"):
        cm = inner.contactMessage
        dname = str(getattr(cm, "displayName", "") or "Contact")
        return {
            "msg_type": "contact",
            "body": f"👤 {dname}",
            "preview": f"👤 {dname}",
            "media_mime": "",
            "quoted_id": "",
            "is_view_once": False,
            "media_pb": None,
            "filename": "",
        }

    if _has_field(inner, "contactsArrayMessage"):
        return {
            "msg_type": "contact",
            "body": "👥 Contacts",
            "preview": "👥 Contacts",
            "media_mime": "",
            "quoted_id": "",
            "is_view_once": False,
            "media_pb": None,
            "filename": "",
        }

    if _has_field(inner, "locationMessage") or _has_field(inner, "liveLocationMessage"):
        return {
            "msg_type": "location",
            "body": "📍 Location",
            "preview": "📍 Location",
            "media_mime": "",
            "quoted_id": "",
            "is_view_once": False,
            "media_pb": None,
            "filename": "",
        }

    if (
        _has_field(inner, "pollCreationMessage")
        or _has_field(inner, "pollCreationMessageV2")
        or _has_field(inner, "pollCreationMessageV3")
    ):
        return {
            "msg_type": "poll",
            "body": "📊 Poll",
            "preview": "📊 Poll",
            "media_mime": "",
            "quoted_id": "",
            "is_view_once": False,
            "media_pb": None,
            "filename": "",
        }

    if _has_field(inner, "call") or _has_field(inner, "bcallMessage") or _has_field(inner, "callLogMesssage"):
        return {
            "msg_type": "call",
            "body": "📞 Call",
            "preview": "📞 Call",
            "media_mime": "",
            "quoted_id": "",
            "is_view_once": False,
            "media_pb": None,
            "filename": "",
        }

    return None


@dataclass
class Msg:
    chat: Any          # JID object (used for replies)
    chat_id: str       # "user@server"
    sender_id: str
    text: str
    is_group: bool
    from_me: bool
    raw: Any           # original MessageEv


class WhatsAppCore:
    def __init__(self, private_dir, plugin_dir, emit, *, process_own_messages=False):
        self.emit = emit                                   # emit("/addr", *values) -> UI
        self.private_dir = private_dir
        self.db = os.path.join(private_dir, "session.sqlite3")
        self.process_own = process_own_messages
        self.client = None
        self._ws_ready = None   # asyncio.Event: set when WS connected
        self.sync_state = "stopped"
        self._session_ready = asyncio.Event()
        self._sync_fallback_task: Optional[asyncio.Task] = None
        self._bg_idle_task: Optional[asyncio.Task] = None
        self._app_in_foreground = True
        self._resync_done_this_session = False
        self._bg_tasks: set[asyncio.Task] = set()
        self._job_meta: dict[int, dict[str, Any]] = {}

        self.client_db = ClientDB.get(private_dir)
        self.ghost_config = GhostConfig.load(private_dir)
        self.ghost_guard = GhostGuard(self.ghost_config)

        self.queue = OutboundQueue(self._send_job, typing=self._typing, ready_check=self.wait_until_ready)
        self.supervisor = PluginSupervisor(
            on_quarantine=lambda n, why: self.emit("/log", f"plugin {n} quarantined: {why}"),
            on_error=lambda n, err: self.emit("/log", f"plugin {n} error: {err}"))
        self.plugins = PluginManager(plugin_dir, self)
        self.calls = CallEngine(self, private_dir)
        self._run_task = None
        self.status = "stopped"

    # ---- sync state & readiness helpers
    def _spawn_bg(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)
        return t

    def _cancel_sync_fallback(self):
        if self._sync_fallback_task and not self._sync_fallback_task.done():
            self._sync_fallback_task.cancel()
        self._sync_fallback_task = None

    def _set_sync_state(self, state: str):
        """Transition sync state machine: stopped -> connecting -> ws_connected -> syncing_offline -> ready."""
        valid = {"stopped", "connecting", "ws_connected", "syncing_offline", "ready"}
        if state not in valid:
            return
        if state in ("stopped", "connecting"):
            self._cancel_sync_fallback()
            self._session_ready.clear()
        elif state in ("ws_connected", "syncing_offline") and self.sync_state == "ready":
            # Do not regress from ready unless disconnected/reconnecting
            return
        elif state == "ready":
            self._cancel_sync_fallback()
            self._session_ready.set()

        changed = (self.sync_state != state)
        self.sync_state = state
        if changed:
            self.emit("/sync_state", state)

    async def _is_client_connected(self) -> bool:
        if not self.client:
            return False
        try:
            val = self.client.is_connected
            if asyncio.iscoroutine(val) or asyncio.isfuture(val) or hasattr(val, "__await__"):
                return bool(await val)
            return bool(val)
        except Exception:
            return False

    async def _is_client_logged_in(self) -> bool:
        if not self.client:
            return False
        try:
            val = self.client.is_logged_in
            if asyncio.iscoroutine(val) or asyncio.isfuture(val) or hasattr(val, "__await__"):
                return bool(await val)
            return bool(val)
        except Exception:
            return False

    async def wait_until_ready(self, timeout: float = 6.0) -> bool:
        """Wait for session readiness + live WebSocket connection before sending."""
        if self.sync_state == "stopped":
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout))
        if not self._session_ready.is_set():
            rem = deadline - loop.time()
            if rem <= 0:
                return False
            try:
                await asyncio.wait_for(self._session_ready.wait(), timeout=rem)
            except asyncio.TimeoutError:
                return False

        if self.sync_state == "stopped":
            return False
        if not self.client:
            return True

        ws_deadline = min(deadline, loop.time() + 1.5)
        while True:
            if await self._is_client_connected():
                return True
            if loop.time() >= ws_deadline or self.sync_state == "stopped":
                return False
            await asyncio.sleep(0.15)

    async def _finish_session_ready(self):
        self._set_sync_state("ready")
        await self.apply_presence()
        if not self._resync_done_this_session:
            self._resync_done_this_session = True
            self._spawn_bg(self.resync_contacts())

    async def apply_presence(self, online: Optional[bool] = None):
        """Apply active/online or unavailable presence respecting GhostGuard."""
        if not self.client or self.status not in ("connected", "paired"):
            return
        if not await self._is_client_connected():
            return
        from neonize.utils.enum import Presence

        want_online = self._app_in_foreground if online is None else bool(online)
        if not self.ghost_guard.allow_presence():
            want_online = False

        try:
            if want_online:
                try:
                    await self.client.set_passive(False)
                except Exception as e:
                    log.debug("set_passive(False) error ignored: %s", e)
                await self.client.send_presence(Presence.AVAILABLE)
            else:
                await self.client.send_presence(Presence.UNAVAILABLE)
        except Exception as e:
            log.debug("apply_presence(%s) failed: %s", want_online, e)

    async def set_app_lifecycle(self, state: str):
        """Handle UI foreground/background transitions for presence control."""
        st = str(state or "").strip().lower()
        if st in ("foreground", "resume", "active"):
            if self._bg_idle_task and not self._bg_idle_task.done():
                self._bg_idle_task.cancel()
            self._bg_idle_task = None
            self._app_in_foreground = True
            if self._session_ready.is_set() and self.ghost_guard.allow_presence():
                await self.apply_presence(True)
        elif st in ("background", "pause", "paused"):
            self._app_in_foreground = False
            if self._bg_idle_task and not self._bg_idle_task.done():
                self._bg_idle_task.cancel()

            async def _idle_offline():
                try:
                    await asyncio.sleep(4.0)
                    if not self._app_in_foreground:
                        await self.apply_presence(False)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.debug("background presence update failed: %s", e)

            self._bg_idle_task = asyncio.create_task(_idle_offline())

    # ---- lifecycle
    async def start(self):
        try:
            self._session_ready.clear()
            self._resync_done_this_session = False
            self._cancel_sync_fallback()
            self.status = "connecting"
            self._set_sync_state("connecting")

            from neonize.aioze.client import NewAClient
            from neonize.aioze.events import (
                AppStateSyncCompleteEv,
                ChatPresenceEv,
                ConnectFailureEv,
                ConnectedEv,
                ContactSyncEv,
                DisconnectedEv,
                HistorySyncEv,
                LoggedOutEv,
                MessageDeleteForMeEv,
                MessageEv,
                OfflineSyncCompletedEv,
                OfflineSyncPreviewEv,
                PairStatusEv,
                PresenceEv,
                ReceiptEv,
                StreamErrorEv,
            )

            self.client = c = NewAClient(self.db)
            self.calls.attach_client(c, asyncio.get_running_loop())
            self.emit("/log", "WhatsApp engine initialized. Connecting to servers...")

            @c.event(ConnectedEv)
            async def _connected(_, __):
                self.status = "connected"
                self.emit("/status", "connected")
                self.emit("/log", "WhatsApp client connected successfully.")
                if self._ws_ready:
                    self._ws_ready.set()
                if self.sync_state != "ready":
                    self._set_sync_state("ws_connected")
                self._cancel_sync_fallback()

                async def _fallback_ready():
                    try:
                        await asyncio.sleep(3.5)
                        if not self._session_ready.is_set() and self.status in ("connected", "paired"):
                            await self._finish_session_ready()
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        log.debug("sync fallback failed: %s", e)

                self._sync_fallback_task = asyncio.create_task(_fallback_ready())

            @c.event(OfflineSyncPreviewEv)
            async def _offline_preview(_, ev):
                self._set_sync_state("syncing_offline")

            @c.event(OfflineSyncCompletedEv)
            async def _offline_completed(_, ev):
                await self._finish_session_ready()

            @c.event(AppStateSyncCompleteEv)
            async def _appstate_complete(_, ev):
                if not self._session_ready.is_set():
                    await self._finish_session_ready()
                else:
                    await self.apply_presence()

            @c.event(ConnectFailureEv)
            async def _conn_fail(_, ev):
                self.status = "disconnected"
                self._resync_done_this_session = False
                if self._ws_ready:
                    self._ws_ready.clear()
                self._set_sync_state("connecting")
                self.emit("/log", f"WhatsApp connection failure: {ev}")

            @c.event(StreamErrorEv)
            async def _stream_err(_, ev):
                self.status = "disconnected"
                self._resync_done_this_session = False
                if self._ws_ready:
                    self._ws_ready.clear()
                self._set_sync_state("connecting")
                self.emit("/log", f"WhatsApp stream error: {ev}")

            @c.event(DisconnectedEv)
            async def _disconnected(_, __):
                self.status = "disconnected"
                self._resync_done_this_session = False
                if self._ws_ready:
                    self._ws_ready.clear()
                self._set_sync_state("stopped")
                self.emit("/status", "disconnected")
                self.emit("/log", "WhatsApp client disconnected.")

            @c.event(LoggedOutEv)
            async def _logged_out(_, ev):
                self.status = "disconnected"
                self._resync_done_this_session = False
                if self._ws_ready:
                    self._ws_ready.clear()
                self._set_sync_state("stopped")
                self.emit("/status", "disconnected")
                self.emit("/log", f"WhatsApp session logged out: {ev}")

            @c.event(PairStatusEv)
            async def _paired(_, ev):
                self.status = "paired"
                self.emit("/status", "paired")
                self.emit("/log", "Device paired successfully with WhatsApp.")

            @c.event(HistorySyncEv)
            async def _history_sync(_, ev):
                await self._handle_history_sync(ev)

            @c.event(ContactSyncEv)
            async def _contact_sync(_, ev):
                self._handle_contact_sync(ev)

            @c.event(ReceiptEv)
            async def _receipt(_, ev):
                self._handle_receipt(ev)

            @c.event(PresenceEv)
            async def _presence(_, ev):
                self._handle_presence(ev)

            @c.event(ChatPresenceEv)
            async def _chat_presence(_, ev):
                self._handle_chat_presence(ev, ChatPresenceEv)

            @c.event(MessageDeleteForMeEv)
            async def _delete_for_me(_, ev):
                self._handle_delete_for_me(ev)

            @c.event(MessageEv)
            async def _message(_, ev):
                await self._handle_message(ev)

            async def _qr(_, data: bytes):
                try:
                    txt = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
                except Exception:
                    txt = str(data)

                # Save QR to PNG via segno if available
                try:
                    import segno
                    cache_file = qr_cache_path()
                    qr_obj = segno.make(txt)
                    qr_obj.save(cache_file, scale=6)
                    self.emit("/qr", cache_file)
                except Exception as e:
                    log.debug("segno qr save failed: %s", e)
                    self.emit("/qr", txt)

            try:
                c.event.qr(_qr)
            except AttributeError:
                log.info("this neonize build has no qr event hook; use pairing code")

            self._ws_ready = asyncio.Event()
            self.queue.start()
            self.supervisor.start_monitor()
            rep = await self.plugins.reload()
            self.emit("/plugins", self.plugins.formatted_list())
            self.emit("/log", f"plugins: {rep}")
            self.emit("/engine_state", "running")
            self._run_task = asyncio.create_task(self._run())
        except Exception as e:
            log.exception("Engine start failed")
            self._set_sync_state("stopped")
            self.emit("/status", "init_error")
            self.emit("/engine_state", "stopped")
            self.emit("/log", f"Engine init error: {e!r}")

    # ---- incoming sync/event handlers
    @staticmethod
    def _serialize_pb_raw(media_pb: Any) -> str:
        if media_pb is None:
            return ""
        ser = getattr(media_pb, "SerializeToString", None)
        if not callable(ser):
            return ""
        try:
            raw_bytes = ser()
            if raw_bytes:
                return json.dumps({"pb_b64": base64.b64encode(raw_bytes).decode("ascii")})
        except Exception:
            pass
        return ""

    async def _save_viewonce_inline(self, msg_id: str, payload: dict[str, Any], inner_msg: Any) -> str:
        if not self.client or not payload.get("is_view_once") or inner_msg is None:
            return ""
        try:
            data = await self.client.download_any(inner_msg)
            if not data:
                return ""
            vo_dir = os.path.join(self.private_dir, "view_once_inline")
            os.makedirs(vo_dir, exist_ok=True)
            ext = _ext_for_media(payload["msg_type"], payload.get("media_mime", ""), payload.get("filename", ""))
            safe_id = "".join(ch for ch in str(msg_id) if ch.isalnum() or ch in ("-", "_"))[:64] or uuid.uuid4().hex[:12]
            filepath = os.path.join(vo_dir, f"vo_{safe_id}{ext}")
            with open(filepath, "wb") as f:
                f.write(data)
            return filepath
        except Exception as e:
            log.debug("viewonce inline save failed for %s: %s", msg_id, e)
            return ""

    async def _handle_history_sync(self, ev):
        try:
            data = getattr(ev, "Data", None)
            if not data:
                return
            for pn in (getattr(data, "pushnames", ()) or ()):
                p_jid = _jid_str(getattr(pn, "ID", ""))
                p_name = str(getattr(pn, "pushname", "") or "").strip()
                if p_jid and p_name:
                    self.client_db.upsert_contact(p_jid, push_name=p_name)

            for conv in (getattr(data, "conversations", ()) or ()):
                chat_jid = _jid_str(getattr(conv, "ID", ""))
                if not chat_jid or chat_jid == "status@broadcast":
                    continue
                conv_name = str(getattr(conv, "name", "") or getattr(conv, "displayName", "") or "").strip()
                unread = int(getattr(conv, "unreadCount", 0) or 0)
                pinned = bool(getattr(conv, "pinned", 0))
                archived = bool(getattr(conv, "archived", False))
                mute_end = _norm_ts(getattr(conv, "muteEndTime", 0))
                conv_last_ts = _norm_ts(
                    getattr(conv, "lastMsgTimestamp", 0) or getattr(conv, "conversationTimestamp", 0)
                )

                last_ts = 0
                last_mid = ""
                last_prev = ""

                for hsm in (getattr(conv, "messages", ()) or ()):
                    wmi = getattr(hsm, "message", None)
                    if not wmi:
                        continue
                    key = getattr(wmi, "key", None)
                    msg_id = str(getattr(key, "ID", "") or "")
                    if not msg_id:
                        continue
                    from_me = bool(getattr(key, "fromMe", False))
                    remote_jid = _jid_str(getattr(key, "remoteJID", "") or chat_jid) or chat_jid
                    participant = _jid_str(getattr(key, "participant", "") or getattr(wmi, "participant", ""))
                    sender_id = participant or ("" if from_me else remote_jid)
                    ts_val = _norm_ts(getattr(wmi, "messageTimestamp", 0)) or conv_last_ts
                    push_name = str(getattr(wmi, "pushName", "") or "").strip()

                    if push_name and sender_id and not from_me:
                        self.client_db.upsert_contact(sender_id, push_name=push_name)

                    m = getattr(wmi, "message", None)
                    if m is None:
                        continue

                    if _has_field(m, "protocolMessage"):
                        proto = m.protocolMessage
                        target_id = str(getattr(proto.key, "ID", "") or "")
                        if proto.type == 0 and target_id:
                            self.client_db.mark_message_deleted(target_id, chat_id=remote_jid)
                        elif proto.type == 14 and target_id:
                            new_txt = _extract_text_from_proto(getattr(proto, "editedMessage", None))
                            if new_txt:
                                self.client_db.mark_message_edited(target_id, new_txt, chat_id=remote_jid)
                        continue

                    if _has_field(m, "reactionMessage"):
                        rx = m.reactionMessage
                        target_id = str(getattr(rx.key, "ID", "") or "")
                        if target_id:
                            self.client_db.add_reaction(
                                target_id, sender_id or remote_jid, str(getattr(rx, "text", "") or "")
                            )
                        continue

                    payload = _extract_message_payload(m)
                    if not payload:
                        continue

                    wmi_status = int(getattr(wmi, "status", 2) or 2)
                    receipt_status = {
                        0: "pending",
                        1: "pending",
                        2: "sent",
                        3: "delivered",
                        4: "read",
                        5: "played",
                    }.get(wmi_status, "sent")

                    raw_json = self._serialize_pb_raw(payload.get("media_pb"))
                    self.client_db.upsert_message(
                        msg_id,
                        remote_jid,
                        sender_id=sender_id,
                        from_me=from_me,
                        ts=ts_val,
                        msg_type=payload["msg_type"],
                        body=payload["body"],
                        media_mime=payload["media_mime"],
                        quoted_id=payload["quoted_id"],
                        receipt_status=receipt_status,
                        is_view_once=payload["is_view_once"],
                        raw_json=raw_json,
                    )
                    if ts_val >= last_ts:
                        last_ts = ts_val
                        last_mid = msg_id
                        last_prev = payload["preview"]

                self.client_db.upsert_chat(
                    chat_jid,
                    name=conv_name or None,
                    is_group=chat_jid.endswith("@g.us"),
                    last_msg_id=last_mid or None,
                    last_preview=last_prev or None,
                    last_ts=max(conv_last_ts, last_ts),
                    unread_count=unread,
                    muted_until=mute_end,
                    pinned=pinned,
                    archived=archived,
                )

            self.emit("/chat_updated", "all")
            self.emit("/contact_updated", "sync")
        except Exception as e:
            log.debug("HistorySyncEv error: %s", e, exc_info=True)

    def _handle_contact_sync(self, ev):
        try:
            jid_str = _jid_str(getattr(ev, "JID", None))
            act = getattr(ev, "Action", None)
            full_name = str(getattr(act, "fullName", "") or "").strip() if act else ""
            first_name = str(getattr(act, "firstName", "") or "").strip() if act else ""
            pn_jid = _jid_str(getattr(act, "pnJID", "")) if act else ""
            best_name = full_name or first_name
            phone = ""
            if pn_jid and "@" in pn_jid:
                phone = pn_jid.split("@", 1)[0]
            elif jid_str and "@" in jid_str and not jid_str.endswith("@g.us"):
                phone = jid_str.split("@", 1)[0]

            if jid_str:
                self.client_db.upsert_contact(jid_str, phone=phone, full_name=best_name)
                if pn_jid and pn_jid != jid_str:
                    self.client_db.upsert_contact(pn_jid, phone=phone, full_name=best_name)
                self.emit("/contact_updated", jid_str)
        except Exception as e:
            log.debug("ContactSyncEv error: %s", e, exc_info=True)

    def _handle_receipt(self, ev):
        try:
            src = getattr(ev, "MessageSource", None)
            chat_jid = _jid_str(getattr(src, "Chat", None))
            msg_ids = [str(mid) for mid in (getattr(ev, "MessageIDs", ()) or ()) if mid]
            rtype = int(getattr(ev, "Type", 0) or 0)
            status_map = {
                1: "delivered",
                4: "read",
                5: "read",
                6: "played",
                7: "played",
            }
            status_str = status_map.get(rtype)
            if chat_jid and msg_ids and status_str:
                self.client_db.update_receipt(chat_jid, msg_ids, status_str)
                if rtype == 5:
                    self.client_db.mark_incoming_chat_read_local(chat_jid)
                self.emit("/chat_updated", chat_jid)
        except Exception as e:
            log.debug("ReceiptEv error: %s", e, exc_info=True)

    def _handle_presence(self, ev):
        try:
            jid_str = _jid_str(getattr(ev, "From", None))
            if not jid_str:
                return
            unavail = bool(getattr(ev, "Unavailable", False))
            last_seen = _norm_ts(getattr(ev, "LastSeen", 0))
            self.client_db.set_presence(
                jid_str,
                is_online=(not unavail),
                last_seen_ts=(last_seen if last_seen > 0 else None),
            )
            self.emit(
                "/presence_update",
                json.dumps({
                    "jid": jid_str,
                    "online": (not unavail),
                    "last_seen": last_seen,
                    "typing": False,
                }),
            )
        except Exception as e:
            log.debug("PresenceEv error: %s", e, exc_info=True)

    def _handle_chat_presence(self, ev, chat_presence_cls):
        try:
            src = getattr(ev, "MessageSource", None)
            chat_jid = _jid_str(getattr(src, "Chat", None))
            if not chat_jid:
                return
            state_val = int(getattr(ev, "State", 0) or 0)
            if hasattr(ev, "DESCRIPTOR"):
                is_typing = (state_val == int(getattr(chat_presence_cls, "COMPOSING", 1)))
            else:
                is_typing = (state_val == 0)
            self.client_db.set_chat_typing(chat_jid, is_typing)
            self.emit(
                "/presence_update",
                json.dumps({"jid": chat_jid, "typing": is_typing}),
            )
        except Exception as e:
            log.debug("ChatPresenceEv error: %s", e, exc_info=True)

    def _handle_delete_for_me(self, ev):
        try:
            chat_jid = _jid_str(getattr(ev, "ChatJID", None))
            msg_id = str(getattr(ev, "MessageID", "") or "")
            if msg_id:
                resolved = self.client_db.mark_message_deleted(msg_id, chat_id=chat_jid)
                self.emit("/chat_updated", resolved or chat_jid or "all")
        except Exception as e:
            log.debug("MessageDeleteForMeEv error: %s", e, exc_info=True)

    async def _handle_message(self, ev):
        try:
            info = getattr(ev, "Info", None)
            src = getattr(info, "MessageSource", None)
            m = getattr(ev, "Message", None)
            if info and src and m:
                chat_id = _jid_str(getattr(src, "Chat", None))
                sender_id = _jid_str(getattr(src, "Sender", None))
                from_me = bool(getattr(src, "IsFromMe", False))
                is_group = bool(getattr(src, "IsGroup", False))
                msg_id = str(getattr(info, "ID", "") or "")
                ts_val = _norm_ts(getattr(info, "Timestamp", 0)) or int(time.time())
                push_name = str(getattr(info, "Pushname", "") or "").strip()

                if push_name and not from_me:
                    if sender_id:
                        self.client_db.upsert_contact(sender_id, push_name=push_name)
                    if not is_group and chat_id and chat_id != sender_id:
                        self.client_db.upsert_contact(chat_id, push_name=push_name)

                if _has_field(m, "protocolMessage"):
                    proto = m.protocolMessage
                    target_id = str(getattr(proto.key, "ID", "") or "")
                    if proto.type == 0 and target_id:
                        resolved = self.client_db.mark_message_deleted(target_id, chat_id=chat_id)
                        self.emit("/chat_updated", resolved or chat_id)
                    elif proto.type == 14 and target_id:
                        new_txt = _extract_text_from_proto(getattr(proto, "editedMessage", None))
                        resolved = self.client_db.mark_message_edited(target_id, new_txt, chat_id=chat_id)
                        self.emit("/chat_updated", resolved or chat_id)
                elif _has_field(m, "reactionMessage"):
                    rx = m.reactionMessage
                    target_id = str(getattr(rx.key, "ID", "") or "")
                    if target_id:
                        resolved = self.client_db.add_reaction(
                            target_id, sender_id or chat_id, str(getattr(rx, "text", "") or "")
                        )
                        self.emit("/chat_updated", resolved or chat_id)
                else:
                    payload = _extract_message_payload(m)
                    if payload and msg_id and chat_id:
                        inner_vo, _ = _unwrap_message(m)
                        media_path = ""
                        if payload["is_view_once"]:
                            media_path = await self._save_viewonce_inline(msg_id, payload, inner_vo)
                        raw_json = self._serialize_pb_raw(payload.get("media_pb"))
                        self.client_db.upsert_message(
                            msg_id,
                            chat_id,
                            sender_id=sender_id,
                            from_me=from_me,
                            ts=ts_val,
                            msg_type=payload["msg_type"],
                            body=payload["body"],
                            media_path=media_path,
                            media_mime=payload["media_mime"],
                            quoted_id=payload["quoted_id"],
                            receipt_status="sent" if from_me else "delivered",
                            is_view_once=payload["is_view_once"],
                            raw_json=raw_json,
                        )
                        chat_name = push_name if (not is_group and not from_me and push_name) else None
                        self.client_db.upsert_chat(
                            chat_id,
                            name=chat_name,
                            is_group=is_group,
                            last_msg_id=msg_id,
                            last_preview=payload["preview"],
                            last_ts=ts_val,
                            unread_delta=(0 if from_me else 1),
                        )
                        self.emit("/chat_updated", chat_id)
        except Exception as e:
            log.debug("MessageEv DB persist error: %s", e, exc_info=True)

        msg = self._to_msg(ev)
        if not msg:
            return
        # Allow incoming from others, or self-commands starting with ! or greetings
        if msg.from_me and not self.process_own:
            txt = msg.text.strip().lower()
            first = txt.split()[0] if txt else ""
            if not (first.startswith("!") or first in {"hi", "hello", "hey", "hlo", "namaste", "salam"}):
                return
        self.emit("/log", f"Message from {msg.sender_id}: {msg.text[:50]}")
        self.plugins.dispatch(msg)

    async def _run(self):
        self.emit("/status", "connecting")
        try:
            await self.client.connect()
            await self.client.idle()
        except Exception as e:
            log.exception("neonize loop died")
            self.emit("/status", f"error: {e!r}")

    # ---- commands from UI
    async def pair(self, phone: str):
        try:
            if not self.client or (self._run_task and self._run_task.done()):
                self.emit("/log", "Engine reconnecting, please wait...")
                await self.start()
            if isinstance(phone, bytes):
                phone = phone.decode()
            phone = str(phone).strip().lstrip("+")
            if not phone:
                self.emit("/log", "Please enter a valid phone number with country code.")
                return

            # Check if already paired
            try:
                already_logged = await self.client.is_logged_in
                if already_logged:
                    self.emit("/log", "Already paired! Tap 'Clear Session' first to re-pair.")
                    return
            except Exception:
                pass

            # Wait for WebSocket connection (polling is_connected coroutine)
            ws_ok = await self._is_client_connected()
            if not ws_ok:
                self.emit("/log", "Waiting for WhatsApp WebSocket connection...")
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    if await self._is_client_connected():
                        ws_ok = True
                        break

            if not ws_ok:
                self.emit("/log", "WhatsApp server not connected yet. Check internet connection and retry.")
                return

            self.emit("/log", f"Requesting pairing code for +{phone}...")
            code = await self.client.PairPhone(phone, show_push_notification=True)
            self.emit("/pair_code", str(code))
            self.emit("/log", f"Pairing code received: {code}")
        except Exception as e:
            self.emit("/log", f"Pairing failed: {e!r}")

    async def send_direct(self, recipient: str, text: str) -> bool:
        if not recipient or not text:
            return False
        try:
            if not self.client:
                self.emit("/log", "Cannot send: WhatsApp client is not connected.")
                return False
            if isinstance(recipient, bytes):
                recipient = recipient.decode()
            if isinstance(text, bytes):
                text = text.decode()
            recipient = str(recipient).strip()
            jid = self.jid_for(recipient)
            job = Job(chat=jid, chat_key=recipient, text=str(text), plugin="direct_ui")
            ok = self.queue.put(job)
            if ok:
                self.emit("/log", f"Direct message queued for {recipient}")
            else:
                self.emit("/log", "Failed to queue direct message (queue full)")
            return ok
        except Exception as e:
            self.emit("/log", f"Direct send error: {e}")
            return False

    def _build_quoted_ev(self, chat_jid: str, quoted_id: str):
        if not quoted_id:
            return None
        q_row = self.client_db.get_message(quoted_id, chat_id=chat_jid) or self.client_db.get_message(quoted_id)
        if not q_row:
            return None
        try:
            from neonize.proto.Neonize_pb2 import Message as NeonizeMessageEv, MessageInfo, MessageSource
            from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as E2EMessage

            chat_obj = self.jid_for(chat_jid)
            sender_str = q_row.get("sender_id") or ""
            if sender_str:
                sender_obj = self.jid_for(sender_str)
            elif q_row.get("from_me") and getattr(getattr(self.client, "me", None), "JID", None):
                sender_obj = self.client.me.JID
            else:
                sender_obj = chat_obj
            e2e_msg = None
            raw_json = q_row.get("raw_json") or ""
            if raw_json:
                try:
                    meta = json.loads(raw_json)
                    pb_b64 = meta.get("pb_b64")
                    if pb_b64:
                        e2e_msg = E2EMessage.FromString(base64.b64decode(pb_b64))
                except Exception:
                    e2e_msg = None
            if e2e_msg is None:
                e2e_msg = E2EMessage(conversation=str(q_row.get("body") or ""))

            return NeonizeMessageEv(
                Info=MessageInfo(
                    ID=str(q_row["id"]),
                    MessageSource=MessageSource(
                        Chat=chat_obj,
                        Sender=sender_obj,
                        IsFromMe=bool(q_row.get("from_me")),
                        IsGroup=chat_jid.endswith("@g.us"),
                    ),
                ),
                Message=e2e_msg,
            )
        except Exception as e:
            log.debug("build quoted ev failed: %s", e)
            return None

    async def send_chat_message(self, chat_jid: str, text: str, quoted_id: str = "") -> bool:
        """Queue an interactive chat message from UI with optimistic DB row + optional quote."""
        if not chat_jid or not text:
            return False
        if not self.client:
            self.emit("/log", "Cannot send: WhatsApp client is not initialized.")
            return False
        chat_norm = normalize_jid(str(chat_jid).strip())
        if not chat_norm:
            return False
        text_str = str(text)
        quoted_clean = str(quoted_id or "").strip()

        local_id = f"local_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = int(time.time())
        self.client_db.upsert_message(
            local_id,
            chat_norm,
            sender_id="",
            from_me=True,
            ts=now,
            msg_type="text",
            body=text_str,
            quoted_id=quoted_clean,
            receipt_status="pending",
        )
        self.client_db.upsert_chat(
            chat_norm,
            is_group=chat_norm.endswith("@g.us"),
            last_msg_id=local_id,
            last_preview=text_str,
            last_ts=now,
            unread_delta=0,
        )
        self.emit("/chat_updated", chat_norm)

        jid = self.jid_for(chat_norm)
        quoted_ev = self._build_quoted_ev(chat_norm, quoted_clean)
        job = Job(chat=jid, chat_key=chat_norm, text=text_str, plugin="direct_ui", quoted=quoted_ev)
        self._job_meta[id(job)] = {
            "local_id": local_id,
            "chat_id": chat_norm,
            "quoted_id": quoted_clean,
        }
        ok = self.queue.put(job)
        if not ok:
            self._job_meta.pop(id(job), None)
            self.emit("/log", "Failed to queue chat message (queue full)")
        return ok

    async def send_media_message(
        self, chat_jid: str, kind: str, path: str, caption: str = ""
    ) -> tuple[bool, str]:
        """Send an image, video, audio/voice, document, or sticker to chat_jid and persist to ClientDB."""
        chat_norm = normalize_jid(str(chat_jid or "").strip())
        if not chat_norm or not path:
            return False, "Missing chat JID or file path"
        if not os.path.exists(path):
            return False, f"File not found: {path}"
        if not self.client:
            return False, "WhatsApp client not initialized"
        if not await self.wait_until_ready(timeout=6.0):
            return False, "WhatsApp connection not ready"

        k = str(kind or "document").strip().lower()
        cap = str(caption or "")
        to_jid = self.jid_for(chat_norm)
        try:
            if k == "image":
                resp = await self.client.send_image(to_jid, path, caption=cap or None)
                msg_type, mime = "image", "image/jpeg"
                body = cap or "📷 Photo"
                preview = f"📷 {cap}" if cap else "📷 Photo"
            elif k == "video":
                resp = await self.client.send_video(to_jid, path, caption=cap or None)
                msg_type, mime = "video", "video/mp4"
                body = cap or "🎥 Video"
                preview = f"🎥 {cap}" if cap else "🎥 Video"
            elif k in ("audio", "voice", "ptt"):
                resp = await self.client.send_audio(to_jid, path, ptt=(k in ("voice", "ptt")))
                msg_type, mime = "audio", "audio/ogg"
                body = "🎵 Voice Note"
                preview = "🎵 Voice Note"
            elif k == "sticker":
                resp = await self.client.send_sticker(to_jid, path)
                msg_type, mime = "sticker", "image/webp"
                body = "🎭 Sticker"
                preview = "🎭 Sticker"
            else:
                fname = os.path.basename(path)
                resp = await self.client.send_document(to_jid, path, caption=cap or None, filename=fname)
                msg_type, mime = "document", "application/octet-stream"
                body = f"📄 {fname}\n{cap}" if cap else f"📄 {fname}"
                preview = f"📄 {fname}"

            msg_id = str(getattr(resp, "ID", "") or f"local_{uuid.uuid4().hex[:12]}")
            ts_val = _norm_ts(getattr(resp, "Timestamp", 0)) or int(time.time())
            self.client_db.upsert_message(
                msg_id,
                chat_norm,
                from_me=True,
                ts=ts_val,
                msg_type=msg_type,
                body=body,
                media_path=path,
                media_mime=mime,
                receipt_status="sent",
            )
            self.client_db.upsert_chat(
                chat_norm,
                is_group=chat_norm.endswith("@g.us"),
                last_msg_id=msg_id,
                last_preview=preview,
                last_ts=ts_val,
                unread_delta=0,
            )
            self.emit("/chat_updated", chat_norm)
            self.emit("/log", f"✓ Outbound {msg_type} sent to {chat_norm}")
            return True, msg_id
        except Exception as e:
            log.exception("send_media_message failed for %s", chat_norm)
            return False, str(e)

    async def update_privacy(self, cfg_dict: dict) -> dict:
        """Persist GhostConfig updates, refresh GhostGuard, and apply presence immediately."""
        updated = self.ghost_config.update_from_dict(cfg_dict or {})
        self.ghost_config.save(self.private_dir)
        self.ghost_guard = GhostGuard(self.ghost_config)
        await self.apply_presence()
        return updated

    async def mark_chat_read(self, chat_jid: str, *, force: bool = False):
        """Mark unread messages in chat_jid as read locally and (if allowed/forced) on WhatsApp."""
        chat_norm = normalize_jid(str(chat_jid or "").strip())
        if not chat_norm:
            return
        if not force and not self.ghost_guard.allow_mark_read():
            return

        from neonize.utils.enum import ReceiptType

        unread = self.client_db.get_unread_msg_ids(chat_norm)
        if self.client and unread and await self._is_client_connected():
            by_sender: dict[str, list[str]] = {}
            for mid, sid in unread:
                by_sender.setdefault(sid or chat_norm, []).append(mid)
            c_jid = self.jid_for(chat_norm)
            for sid, mids in by_sender.items():
                try:
                    s_jid = self.jid_for(sid)
                    await self.client.mark_read(*mids, chat=c_jid, sender=s_jid, receipt=ReceiptType.READ)
                except Exception as e:
                    log.debug("mark_read failed for %s: %s", chat_norm, e)

        self.client_db.mark_incoming_chat_read_local(chat_norm)
        self.emit("/chat_updated", chat_norm)

    async def resync_contacts(self) -> int:
        """Fetch all WhatsApp contacts and joined groups into ClientDB."""
        if not self.client or not await self._is_client_connected():
            return 0
        count = 0
        try:
            contacts = await self.client.contact.get_all_contacts()
            for ct in (contacts or ()):
                jid = _jid_str(getattr(ct, "JID", None))
                if not jid:
                    continue
                info = getattr(ct, "Info", None)
                full_name = str(getattr(info, "FullName", "") or getattr(info, "FirstName", "") or "").strip()
                push_name = str(getattr(info, "PushName", "") or "").strip()
                biz_name = str(getattr(info, "BusinessName", "") or "").strip()
                redacted = str(getattr(info, "RedactedPhone", "") or "").strip()
                phone = jid.split("@", 1)[0] if ("@" in jid and not jid.endswith("@g.us")) else redacted
                self.client_db.upsert_contact(
                    jid,
                    phone=phone,
                    push_name=push_name,
                    full_name=full_name,
                    business_name=biz_name,
                )
                count += 1
        except Exception as e:
            log.debug("get_all_contacts failed: %s", e)

        try:
            groups = await self.client.get_joined_groups()
            for grp in (groups or ()):
                gjid = _jid_str(getattr(grp, "JID", None))
                if not gjid:
                    continue
                gname = str(getattr(getattr(grp, "GroupName", None), "Name", "") or "").strip()
                self.client_db.upsert_contact(gjid, full_name=gname)
                self.client_db.upsert_chat(gjid, name=gname or None, is_group=True)
                count += 1
        except Exception as e:
            log.debug("get_joined_groups failed: %s", e)

        self.emit("/contact_updated", str(count))
        self.emit("/chat_updated", "all")
        return count

    async def check_number_on_wa(self, phone: str) -> dict:
        """Verify whether a phone number is registered on WhatsApp."""
        clean = "".join(ch for ch in str(phone or "") if ch.isdigit())
        if not clean:
            return {"ok": False, "registered": False, "phone": "", "error": "Invalid phone number"}
        if not self.client or not await self._is_client_connected():
            return {"ok": False, "registered": False, "phone": clean, "error": "WhatsApp not connected"}
        try:
            results = await self.client.is_on_whatsapp(clean)
            item = results[0] if results else None
            is_in = bool(getattr(item, "IsIn", False))
            jid = _jid_str(getattr(item, "JID", None))
            if is_in and not jid:
                jid = f"{clean}@s.whatsapp.net"
            if is_in and jid:
                self.client_db.upsert_contact(jid, phone=clean)
            return {
                "ok": True,
                "registered": is_in,
                "jid": jid,
                "query": str(getattr(item, "Query", clean) or clean),
                "phone": clean,
            }
        except Exception as e:
            return {"ok": False, "registered": False, "phone": clean, "error": str(e)}

    async def subscribe_chat_presence(self, jid: str):
        """Subscribe to online/last-seen updates for a user JID (best-effort)."""
        jid_clean = normalize_jid(str(jid or "").strip())
        if not jid_clean or jid_clean.endswith("@g.us"):
            return
        if not self.client or not await self._is_client_connected():
            return
        try:
            await self.client.subscribe_presence(self.jid_for(jid_clean))
        except Exception as e:
            log.debug("subscribe_presence(%s) ignored: %s", jid_clean, e)

    async def download_message_media(self, chat_id: str, msg_id: str) -> tuple[bool, str]:
        """Download/decrypt media for an existing ClientDB message on demand."""
        chat_norm = normalize_jid(str(chat_id or "").strip())
        msg_id = str(msg_id or "").strip()
        if not msg_id:
            return False, "Missing message ID"
        row = self.client_db.get_message(msg_id, chat_id=chat_norm or None)
        if not row:
            return False, "Message not found"
        existing_path = row.get("media_path") or ""
        if existing_path and os.path.exists(existing_path):
            return True, existing_path

        raw_json = row.get("raw_json") or ""
        pb_b64 = ""
        if raw_json:
            try:
                pb_b64 = json.loads(raw_json).get("pb_b64", "")
            except Exception:
                pb_b64 = ""
        if not pb_b64:
            return False, "No downloadable media payload stored for this message"
        if not self.client or not await self._is_client_connected():
            return False, "WhatsApp not connected"

        try:
            from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message as E2EMessage

            pb_msg = E2EMessage.FromString(base64.b64decode(pb_b64))
            inner_pb, is_vo = _unwrap_message(pb_msg)
            target_pb = inner_pb if inner_pb is not None else pb_msg
            data = await self.client.download_any(target_pb)
            if not data:
                return False, "Downloaded media was empty"

            sub_dir = "view_once_inline" if (row.get("is_view_once") or is_vo) else "media_cache"
            cache_dir = os.path.join(self.private_dir, sub_dir)
            os.makedirs(cache_dir, exist_ok=True)
            ext = _ext_for_media(row.get("msg_type", ""), row.get("media_mime", ""))
            safe_id = "".join(ch for ch in msg_id if ch.isalnum() or ch in ("-", "_"))[:64] or uuid.uuid4().hex[:12]
            filepath = os.path.join(cache_dir, f"{safe_id}{ext}")
            with open(filepath, "wb") as f:
                f.write(data)
            self.client_db.update_media_path(msg_id, filepath, row.get("media_mime", ""))
            self.emit("/chat_updated", normalize_jid(row.get("chat_id") or chat_norm))
            return True, filepath
        except Exception as e:
            log.debug("download_message_media failed (%s): %s", msg_id, e)
            return False, str(e)

    async def send_reaction_message(self, chat_jid: str, msg_id: str, emoji: str) -> tuple[bool, str]:
        """Send an emoji reaction to a WhatsApp message and store it in ClientDB."""
        chat_norm = normalize_jid(str(chat_jid or "").strip())
        mid = str(msg_id or "").strip()
        rx_emoji = str(emoji or "").strip()
        if not chat_norm or not mid:
            return False, "Missing chat_jid or msg_id"
        if not self.client or not await self._is_client_connected():
            return False, "WhatsApp not connected"

        try:
            row = self.client_db.get_message(mid, chat_id=chat_norm)
            sender_str = ""
            if row:
                if row.get("from_me"):
                    try:
                        me = await self.client.get_me()
                        sender_str = _jid_str(getattr(me, "JID", None))
                    except Exception:
                        sender_str = ""
                else:
                    sender_str = str(row.get("sender_id") or "")
            chat_obj = self.jid_for(chat_norm)
            sender_obj = self.jid_for(sender_str or chat_norm)
            msg_obj = await self.client.build_reaction(chat_obj, sender_obj, mid, rx_emoji)
            await self.client.send_message(chat_obj, msg_obj)

            me_actor = "me"
            try:
                me = await self.client.get_me()
                me_actor = _jid_str(getattr(me, "JID", None)) or "me"
            except Exception:
                pass
            self.client_db.add_reaction(mid, me_actor, rx_emoji)
            self.emit("/chat_updated", chat_norm)
            return True, mid
        except Exception as e:
            log.debug("send_reaction_message(%s, %s) failed: %s", chat_norm, mid, e)
            return False, str(e)

    async def chat_flag_action(self, chat_jid: str, action: str):
        """Pin/unpin, archive/unarchive, or mute/unmute a chat in WhatsApp & ClientDB."""
        chat_norm = normalize_jid(str(chat_jid or "").strip())
        act = str(action or "").strip().lower()
        if not chat_norm or not act:
            return
        jid_obj = self.jid_for(chat_norm)
        connected = bool(self.client and await self._is_client_connected())

        try:
            if act in ("pin", "unpin"):
                val = (act == "pin")
                if connected and hasattr(self.client, "chat_settings"):
                    await self.client.chat_settings.put_pinned(jid_obj, val)
                self.client_db.set_chat_flags(chat_norm, pinned=val)
            elif act in ("archive", "unarchive"):
                val = (act == "archive")
                if connected and hasattr(self.client, "chat_settings"):
                    await self.client.chat_settings.put_archived(jid_obj, val)
                self.client_db.set_chat_flags(chat_norm, archived=val)
            elif act in ("mute", "unmute"):
                if act == "mute":
                    delta = timedelta(hours=8)
                    until_ts = int(time.time()) + int(delta.total_seconds())
                else:
                    delta = timedelta(seconds=0)
                    until_ts = 0
                if connected and hasattr(self.client, "chat_settings"):
                    await self.client.chat_settings.put_muted_until(jid_obj, delta)
                self.client_db.set_chat_flags(chat_norm, muted_until=until_ts)
            self.emit("/chat_updated", chat_norm)
        except Exception as e:
            log.debug("chat_flag_action(%s, %s) failed: %s", chat_norm, act, e)

    async def clear_session(self):
        try:
            self._cancel_sync_fallback()
            if self._bg_idle_task and not self._bg_idle_task.done():
                self._bg_idle_task.cancel()
            self._bg_idle_task = None
            self._session_ready.clear()
            self._resync_done_this_session = False
            self._set_sync_state("stopped")

            if self._run_task and not self._run_task.done():
                self._run_task.cancel()
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            if os.path.exists(self.db):
                try:
                    os.remove(self.db)
                except Exception:
                    pass
            for ext in ("-shm", "-wal"):
                extra = self.db + ext
                if os.path.exists(extra):
                    try:
                        os.remove(extra)
                    except Exception:
                        pass
            cache_file = qr_cache_path()
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                except Exception:
                    pass

            self.emit("/status", "disconnected")
            self.emit("/pair_code", "")
            self.emit("/log", "Session cleared. Re-initializing fresh client...")
            await asyncio.sleep(0.5)
            await self.start()
        except Exception as e:
            self.emit("/log", f"Clear session failed: {e}")

    async def stop(self):
        """Disconnect and put the bot in paused/offline state."""
        try:
            self._cancel_sync_fallback()
            if self._bg_idle_task and not self._bg_idle_task.done():
                self._bg_idle_task.cancel()
            self._bg_idle_task = None
            self._resync_done_this_session = False

            if self._run_task and not self._run_task.done():
                self._run_task.cancel()
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            self.status = "stopped"
            self._set_sync_state("stopped")
            self.emit("/status", "stopped")
            self.emit("/engine_state", "stopped")
            self.emit("/log", "Bot stopped. WhatsApp client disconnected (Offline).")
        except Exception as e:
            log.exception("Stop failed")
            self.emit("/log", f"Stop failed: {e}")

    async def resume(self):
        """Resume bot and reconnect to WhatsApp servers."""
        try:
            self.status = "connecting"
            self._set_sync_state("connecting")
            self.emit("/status", "connecting")
            self.emit("/engine_state", "connecting")
            self.emit("/log", "Starting bot engine and connecting...")
            await self.start()
        except Exception as e:
            log.exception("Resume failed")
            self.emit("/log", f"Start failed: {e}")

    async def reload_plugins(self):
        rep = await self.plugins.reload()
        self.emit("/plugins", self.plugins.formatted_list())
        self.emit("/log", f"Plugins reloaded: {rep}")

    async def snapshot(self):
        self.emit("/plugins", self.plugins.formatted_list())
        self.emit("/stats", str(self.supervisor.report()))
        self.emit("/sync_state", self.sync_state)
        self.emit("/privacy_state", json.dumps(self.ghost_config.to_dict()))
        self.emit("/chat_updated", "all")
        try:
            self.emit("/call_tracks", json.dumps(call_media.list_media_tracks()))
            self.emit("/call_history", json.dumps(self.calls.history(25)))
            self.emit("/call_event", json.dumps({
                "event": "snapshot",
                "supported": self.calls.is_supported(),
                "active_calls": list(self.calls._active.values()),
            }))
        except Exception:
            pass
        state = "running" if (self._run_task and not self._run_task.done()) else "stopped"
        self.emit("/engine_state", state)
        if state == "stopped":
            self.status = "stopped"
            self.emit("/status", "stopped")
        elif self.client:
            try:
                logged = await self._is_client_logged_in()
                conn = await self._is_client_connected()
                if logged and conn:
                    self.status = "connected"
                    self.emit("/status", "connected")
                elif conn:
                    self.status = "ready_to_pair"
                    self.emit("/status", "ready_to_pair")
                else:
                    self.status = "connecting"
                    self.emit("/status", "connecting")
            except Exception:
                pass

    # ---- helpers
    def jid_for(self, number: str):
        from neonize.utils import build_jid
        clean = str(number).strip().lstrip("+")
        if "@" in clean:
            user, server = clean.split("@", 1)
            user = user.split(":", 1)[0]
            return build_jid(user, server)
        return build_jid(clean, "s.whatsapp.net")

    @staticmethod
    def _to_msg(ev):
        try:
            src, m = ev.Info.MessageSource, ev.Message
            text = m.conversation or m.extendedTextMessage.text or ""
            return Msg(chat=src.Chat, chat_id=_jid_str(src.Chat),
                       sender_id=_jid_str(src.Sender), text=text,
                       is_group=src.IsGroup, from_me=src.IsFromMe, raw=ev)
        except Exception:
            log.debug("unparsed message", exc_info=True)
            return None

    async def _typing(self, job: Job):
        if not self.ghost_guard.allow_typing():
            return
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia
        try:
            await self.client.send_chat_presence(
                job.chat, ChatPresence.CHAT_PRESENCE_COMPOSING,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
        except Exception:
            pass

    def _finalize_sent_row(self, meta: dict[str, Any], resp: Any, text: str):
        local_id = meta.get("local_id") or ""
        chat_id = meta.get("chat_id") or ""
        quoted_id = meta.get("quoted_id") or ""
        resp_id = str(getattr(resp, "ID", "") or "")
        resp_ts = _norm_ts(getattr(resp, "Timestamp", 0)) or int(time.time())

        if local_id and resp_id:
            try:
                existing = self.client_db.get_message(resp_id)
                with self.client_db._write_lock:
                    c = self.client_db._conn()
                    if existing is None:
                        c.execute(
                            "UPDATE messages SET id = ?, receipt_status = 'sent', ts = ? WHERE id = ?",
                            (resp_id, resp_ts, local_id),
                        )
                    else:
                        c.execute("DELETE FROM messages WHERE id = ?", (local_id,))
                    c.execute(
                        "UPDATE chats SET last_msg_id = ?, last_ts = MAX(last_ts, ?) WHERE jid = ? AND last_msg_id = ?",
                        (resp_id, resp_ts, chat_id, local_id),
                    )
                    c.commit()
                self.emit("/chat_updated", chat_id)
                return
            except Exception as e:
                log.debug("finalize optimistic msg failed: %s", e)

        if resp_id and chat_id:
            self.client_db.upsert_message(
                resp_id,
                chat_id,
                from_me=True,
                ts=resp_ts,
                msg_type="text",
                body=text,
                quoted_id=quoted_id,
                receipt_status="sent",
            )
            self.client_db.upsert_chat(
                chat_id,
                is_group=chat_id.endswith("@g.us"),
                last_msg_id=resp_id,
                last_preview=text,
                last_ts=resp_ts,
                unread_delta=0,
            )
            self.emit("/chat_updated", chat_id)

    async def _send_job(self, job: Job):
        meta = self._job_meta.pop(id(job), {})
        chat_id = meta.get("chat_id") or normalize_jid(job.chat_key)
        try:
            resp = None
            if job.quoted is not None:
                try:
                    resp = await self.client.reply_message(job.text, job.quoted)
                except Exception as quote_err:
                    log.warning("reply_message failed (%s), falling back to send_message", quote_err)
                    resp = await self.client.send_message(job.chat, job.text)
            else:
                resp = await self.client.send_message(job.chat, job.text)
            if resp is not None or meta.get("local_id"):
                self._finalize_sent_row(
                    {"local_id": meta.get("local_id", ""), "chat_id": chat_id, "quoted_id": meta.get("quoted_id", "")},
                    resp,
                    job.text,
                )
            self.emit("/log", f"✓ Outbound message dispatched to {job.chat_key}")
        except Exception as e:
            local_id = meta.get("local_id")
            if local_id:
                try:
                    with self.client_db._write_lock:
                        c = self.client_db._conn()
                        c.execute("UPDATE messages SET receipt_status = 'error' WHERE id = ?", (local_id,))
                        c.commit()
                    self.emit("/chat_updated", chat_id)
                except Exception:
                    pass
            log.exception("Outbound send failed for %s", job.chat_key)
            self.emit("/log", f"✗ Outbound send failed to {job.chat_key}: {e}")
