"""Glue between neonize (async client) and the queue / supervisor / plugin manager.

NOTE: neonize names below follow its async API (neonize.aioze.*) as I know it. Anything marked
VERIFY should be checked against the neonize version you end up building for Android.
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

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
        from neonize.aioze.client import NewAClient                                   # VERIFY
        from neonize.aioze.events import ConnectedEv, DisconnectedEv, MessageEv, PairStatusEv  # VERIFY

        self.client = c = NewAClient(self.db)

        @c.event(ConnectedEv)
        async def _connected(_, __):
            self.emit("/status", "connected")

        @c.event(DisconnectedEv)
        async def _disconnected(_, __):
            self.emit("/status", "disconnected")

        @c.event(PairStatusEv)
        async def _paired(_, ev):
            self.emit("/status", "paired")

        @c.event(MessageEv)
        async def _message(_, ev):
            msg = self._to_msg(ev)
            if msg and (self.process_own or not msg.from_me):
                self.plugins.dispatch(msg)

        async def _qr(_, data: bytes):
            try:
                txt = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
            except Exception:
                txt = str(data)
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
            await self.client.connect()                                                 # VERIFY: may block
            await self.client.idle()                                                    # VERIFY
        except Exception as e:
            log.exception("neonize loop died")
            self.emit("/status", f"error: {e!r}")

    # ---- commands from UI
    async def pair(self, phone: str):
        try:
            if isinstance(phone, bytes):
                phone = phone.decode()
            phone = str(phone).strip().lstrip("+")
            code = await self.client.PairPhone(phone, show_push_notification=True)     # VERIFY
            self.emit("/pair_code", str(code))
        except Exception as e:
            self.emit("/log", f"pairing failed: {e!r}")

    async def reload_plugins(self):
        rep = await self.plugins.reload()
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/log", f"reload: {rep}")

    def snapshot(self):
        self.emit("/plugins", ",".join(self.plugins.names()))
        self.emit("/stats", str(self.supervisor.report()))

    # ---- helpers
    def jid_for(self, number: str):
        from neonize.utils import build_jid                                             # VERIFY
        if "@" in number:
            user, server = number.split("@", 1)
            return build_jid(user, server)
        return build_jid(number, "s.whatsapp.net")

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
        from neonize.utils.enum import ChatPresence, ChatPresenceMedia                  # VERIFY
        await self.client.send_chat_presence(
            job.chat, ChatPresence.CHAT_PRESENCE_COMPOSING,
            ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT)

    async def _send_job(self, job: Job):
        if job.quoted is not None:
            await self.client.reply_message(job.text, job.quoted)
        else:
            await self.client.send_message(job.chat, job.text)
