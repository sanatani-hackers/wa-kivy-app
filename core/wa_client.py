"""Glue between neonize (async client) and the queue / supervisor / plugin manager."""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

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
        self.queue = OutboundQueue(self._send_job, typing=self._typing)
        self.supervisor = PluginSupervisor(
            on_quarantine=lambda n, why: self.emit("/log", f"plugin {n} quarantined: {why}"))
        self.plugins = PluginManager(plugin_dir, self)
        self._run_task = None

    # ---- lifecycle
    async def start(self):
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, DisconnectedEv, MessageEv, PairStatusEv

        self.client = c = NewAClient(self.db)

        @c.event(ConnectedEv)
        async def _connected(_, __):
            self.emit("/status", "connected")
            self.emit("/log", "WhatsApp client connected successfully.")

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

        self.queue.start()
        self.supervisor.start_monitor()
        rep = await self.plugins.reload()
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/log", f"plugins: {rep}")
        self._run_task = asyncio.create_task(self._run())

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
            if isinstance(phone, bytes):
                phone = phone.decode()
            phone = str(phone).strip().lstrip("+")
            code = await self.client.PairPhone(phone, show_push_notification=True)
            self.emit("/pair_code", str(code))
            self.emit("/log", f"Pairing code generated: {code}")
        except Exception as e:
            self.emit("/log", f"Pairing failed: {e!r}")

    async def send_direct(self, recipient: str, text: str) -> bool:
        if not recipient or not text:
            return False
        try:
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
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            if os.path.exists(self.db):
                os.remove(self.db)
            for ext in ("-shm", "-wal"):
                extra = self.db + ext
                if os.path.exists(extra):
                    os.remove(extra)
            self.emit("/status", "disconnected")
            self.emit("/pair_code", "")
            self.emit("/log", "Session cleared. Please re-pair your account.")
        except Exception as e:
            self.emit("/log", f"Clear session failed: {e}")

    async def reload_plugins(self):
        rep = await self.plugins.reload()
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/log", f"Plugins reloaded: {rep}")

    def snapshot(self):
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/stats", str(self.supervisor.report()))

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
