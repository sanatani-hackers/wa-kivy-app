import logging
import time
from collections import OrderedDict

NAME = "antidelete"
log = logging.getLogger("plugin.antidelete")

# In-memory LRU cache: message_id -> message info
MAX_CACHE = 1000
MESSAGE_CACHE: OrderedDict[str, dict] = OrderedDict()


def _get_content_summary(msg_proto) -> str:
    """Extract readable text or media description from raw protobuf."""
    if not msg_proto:
        return "💬 [Empty message]"
    if msg_proto.conversation:
        return msg_proto.conversation
    if msg_proto.extendedTextMessage.text:
        return msg_proto.extendedTextMessage.text
    if msg_proto.HasField("imageMessage"):
        cap = f" - '{msg_proto.imageMessage.caption}'" if msg_proto.imageMessage.caption else ""
        return f"📸 [Photo{cap}]"
    if msg_proto.HasField("videoMessage"):
        cap = f" - '{msg_proto.videoMessage.caption}'" if msg_proto.videoMessage.caption else ""
        return f"🎥 [Video{cap}]"
    if msg_proto.HasField("audioMessage"):
        sec = f" ({msg_proto.audioMessage.seconds}s)" if msg_proto.audioMessage.seconds else ""
        return f"🎵 [Voice Note / Audio{sec}]"
    if msg_proto.HasField("documentMessage"):
        name = msg_proto.documentMessage.fileName or "file"
        cap = f" - '{msg_proto.documentMessage.caption}'" if msg_proto.documentMessage.caption else ""
        return f"📄 [Document: {name}{cap}]"
    if msg_proto.HasField("stickerMessage"):
        return "🎭 [Sticker]"
    return "💬 [Media / Interactive message]"


async def on_message(ctx, msg):
    raw = getattr(msg, "raw", None)
    if not raw or not hasattr(raw, "Message"):
        return

    m = raw.Message
    info = raw.Info

    # ---- 1. Check for ProtocolMessage (REVOKE / EDIT)
    if m.HasField("protocolMessage"):
        proto = m.protocolMessage
        # REVOKE: Delete for Everyone (type 0)
        if proto.type == 0:
            deleted_id = proto.key.ID
            if deleted_id and deleted_id in MESSAGE_CACHE:
                cached = MESSAGE_CACHE.pop(deleted_id)
                sender_display = cached["sender"].split("@")[0]
                alert_text = (
                    "🗑️ *[Anti-Delete Alert]*\n"
                    f"👤 *Sender:* @{sender_display}\n"
                    f"🕒 *Sent at:* {cached['time']}\n"
                    f"💬 *Deleted Message:*\n{cached['text']}"
                )
                log.info("Revoke intercepted for msg %s: %s", deleted_id, cached["text"][:40])
                try:
                    from common import send_android_notification
                    send_android_notification(
                        title=f"🗑️ Deleted by @{sender_display}",
                        text=cached["text"],
                        notif_id=2001,
                    )
                except Exception as e:
                    log.warning("Notification failed: %s", e)
                await ctx.reply(msg, alert_text)
            return

        # MESSAGE_EDIT: Edit message (type 14)
        elif proto.type == 14:
            target_id = proto.key.ID
            if target_id and target_id in MESSAGE_CACHE:
                cached = MESSAGE_CACHE[target_id]
                old_text = cached["text"]
                new_text = _get_content_summary(proto.editedMessage)
                sender_display = cached["sender"].split("@")[0]
                cached["text"] = new_text  # update cache with edited version
                alert_text = (
                    "✏️ *[Anti-Edit Alert]*\n"
                    f"👤 *Sender:* @{sender_display}\n"
                    f"📝 *Original:* {old_text}\n"
                    f"🔄 *Edited to:* {new_text}"
                )
                log.info("Edit intercepted for msg %s", target_id)
                try:
                    from common import send_android_notification
                    send_android_notification(
                        title=f"✏️ Edited by @{sender_display}",
                        text=f"Original: {old_text}\nEdited: {new_text}",
                        notif_id=2002,
                    )
                except Exception as e:
                    log.warning("Notification failed: %s", e)
                await ctx.reply(msg, alert_text)
            return

    # ---- 2. Commands for the user
    text = (msg.text or "").strip()
    if text.lower() == "!antidelete" or text.lower() == "!antidelete status":
        reply = (
            "🛡️ *Anti-Delete & Anti-Edit Status:*\n"
            "• 🟢 *Status:* Active & Monitoring\n"
            f"• 💾 *Cached Messages:* {len(MESSAGE_CACHE)} / {MAX_CACHE}\n"
            "• ⚡ *Actions:* Automatically reveals 'Delete for Everyone' & edits in chat."
        )
        await ctx.reply(msg, reply)
        return
    elif text.lower() == "!antidelete clear":
        count = len(MESSAGE_CACHE)
        MESSAGE_CACHE.clear()
        await ctx.reply(msg, f"🧹 Cache cleared ({count} messages removed).")
        return

    # ---- 3. Cache incoming normal message
    msg_id = getattr(info, "ID", None)
    if msg_id:
        content = _get_content_summary(m)
        ts = getattr(info, "Timestamp", None) or time.time()
        time_str = time.strftime("%H:%M:%S", time.localtime(ts))
        MESSAGE_CACHE[msg_id] = {
            "text": content,
            "sender": msg.sender_id,
            "chat_id": msg.chat_id,
            "time": time_str,
        }
        if len(MESSAGE_CACHE) > MAX_CACHE:
            MESSAGE_CACHE.popitem(last=False)
