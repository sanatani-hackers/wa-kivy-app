"""Glue between neonize (async client) and the queue / supervisor / plugin manager."""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import core._shims
from common import qr_cache_path
from .plugins import PluginManager
from .throttle import Job, OutboundQueue, PluginSupervisor

log = logging.getLogger("wa.core")


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
        self.queue = OutboundQueue(self._send_job, typing=self._typing)
        self.supervisor = PluginSupervisor(
            on_quarantine=lambda n, why: self.emit("/log", f"plugin {n} quarantined: {why}"))
        self.plugins = PluginManager(plugin_dir, self)
        self._run_task = None

    # ---- lifecycle
    async def start(self):
        try:
            from neonize.aioze.client import NewAClient
            from neonize.aioze.events import (
                ConnectedEv, ConnectFailureEv, DisconnectedEv, MessageEv,
                PairStatusEv, StreamErrorEv
            )

            self.client = c = NewAClient(self.db)
            self.emit("/log", "WhatsApp engine initialized. Connecting to servers...")

            @c.event(ConnectedEv)
            async def _connected(_, __):
                self.emit("/status", "connected")
                self.emit("/log", "WhatsApp client connected successfully.")
                if self._ws_ready:
                    self._ws_ready.set()

            @c.event(ConnectFailureEv)
            async def _conn_fail(_, ev):
                self.emit("/log", f"WhatsApp connection failure: {ev}")

            @c.event(StreamErrorEv)
            async def _stream_err(_, ev):
                self.emit("/log", f"WhatsApp stream error: {ev}")

            @c.event(DisconnectedEv)
            async def _disconnected(_, __):
                self.emit("/status", "disconnected")
                self.emit("/log", "WhatsApp client disconnected.")

            @c.event(PairStatusEv)
            async def _paired(_, ev):
                self.emit("/status", "paired")
                self.emit("/log", "Device paired successfully with WhatsApp.")

            @c.event(MessageEv)
            async def _message(_, ev):
                msg = self._to_msg(ev)
                if msg and (self.process_own or not msg.from_me):
                    self.emit("/log", f"Message from {msg.sender_id}: {msg.text[:50]}")
                    self.plugins.dispatch(msg)

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
            self.emit("/plugins", ",".join(self.plugins.names()))
            self.emit("/log", f"plugins: {rep}")
            self._run_task = asyncio.create_task(self._run())
        except Exception as e:
            log.exception("Engine start failed")
            self.emit("/status", "init_error")
            self.emit("/log", f"Engine init error: {e!r}")

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
            try:
                ws_ok = await self.client.is_connected
            except Exception:
                ws_ok = False

            if not ws_ok:
                self.emit("/log", "Waiting for WhatsApp WebSocket connection...")
                for i in range(20):
                    await asyncio.sleep(0.5)
                    try:
                        if await self.client.is_connected:
                            ws_ok = True
                            break
                    except Exception:
                        pass

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
                self.emit("/log", f"Failed to queue direct message (queue full)")
            return ok
        except Exception as e:
            self.emit("/log", f"Direct send error: {e}")
            return False

    async def clear_session(self):
        try:
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

    async def reload_plugins(self):
        rep = await self.plugins.reload()
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/log", f"Plugins reloaded: {rep}")

    async def snapshot(self):
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/stats", str(self.supervisor.report()))
        if self.client:
            try:
                logged = await self.client.is_logged_in
                conn = await self.client.is_connected
                if logged and conn:
                    self.emit("/status", "connected")
                elif conn:
                    self.emit("/status", "ready_to_pair")
            except Exception:
                pass

    # ---- helpers
    def jid_for(self, number: str):
        from neonize.utils import build_jid
        clean = str(number).strip().lstrip("+")
        if "@" in clean:
            user, server = clean.split("@", 1)
            return build_jid(user, server)
        return build_jid(clean, "s.whatsapp.net")

    @staticmethod
    def _to_msg(ev):
        try:
            src, m = ev.Info.MessageSource, ev.Message
            text = m.conversation or m.extendedTextMessage.text or ""
            return Msg(chat=src.Chat, chat_id=f"{src.Chat.User}@{src.Chat.Server}",
                       sender_id=f"{src.Sender.User}@{src.Sender.Server}", text=text,
                       is_group=src.IsGroup, from_me=src.IsFromMe, raw=ev)
        except Exception:
            log.debug("unparsed message", exc_info=True)
            return None

    async def _typing(self, job: Job):
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia
        try:
            await self.client.send_chat_presence(
                job.chat, ChatPresence.CHAT_PRESENCE_COMPOSING,
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)
        except Exception:
            pass

    async def _send_job(self, job: Job):
        if job.quoted is not None:
            await self.client.reply_message(job.text, job.quoted)
        else:
            await self.client.send_message(job.chat, job.text)
        self.emit("/log", f"✓ Outbound message dispatched to {job.chat_key}")
