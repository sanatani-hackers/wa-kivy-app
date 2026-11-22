import logging
import os
import time

NAME = "antiviewonce"
log = logging.getLogger("plugin.antiviewonce")


def _get_writable_dir(ctx) -> str:
    candidates = [
        "/sdcard/Download/WA_ViewOnce",
        os.path.expanduser("~/storage/downloads/WA_ViewOnce"),
        os.path.join(ctx._core.private_dir, "view_once") if hasattr(ctx, "_core") else "/tmp/view_once",
    ]
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            test_file = os.path.join(d, ".perm_test")
            with open(test_file, "w") as f:
                f.write("1")
            os.remove(test_file)
            return d
        except Exception:
            continue
    fallback = os.path.expanduser("~/.wa_app/view_once")
    os.makedirs(fallback, exist_ok=True)
    return fallback


async def on_message(ctx, msg):
    raw = getattr(msg, "raw", None)
    if not raw or not hasattr(raw, "Message"):
        return

    m = raw.Message

    # Check for View-Once wrapper
    inner = None
    if m.HasField("viewOnceMessage"):
        inner = m.viewOnceMessage.message
    elif m.HasField("viewOnceMessageV2"):
        inner = m.viewOnceMessageV2.message
    elif m.HasField("viewOnceMessageV2Extension"):
        inner = m.viewOnceMessageV2Extension.message

    if not inner:
        # User command to check status
        text = (msg.text or "").strip().lower()
        if text in {"!antiviewonce", "!viewonce"}:
            await ctx.reply(
                msg,
                "👁️ *Anti-View-Once Status:*\n"
                "• 🟢 *Status:* Active & Watching\n"
                "• 🔓 *Action:* Intercepts View-Once photos/videos and saves permanent copy."
            )
        return

    # Detected View-Once media!
    media_type = "Media"
    ext = ".bin"
    caption = ""

    if inner.HasField("imageMessage"):
        media_type = "Photo"
        ext = ".jpg"
        caption = inner.imageMessage.caption or ""
    elif inner.HasField("videoMessage"):
        media_type = "Video"
        ext = ".mp4"
        caption = inner.videoMessage.caption or ""
    elif inner.HasField("audioMessage"):
        media_type = "Voice Note"
        ext = ".ogg"

    client = getattr(ctx._core, "client", None) if hasattr(ctx, "_core") else None
    if not client:
        return

    try:
        data = await client.download_any(inner)
    except Exception as e:
        log.warning("Failed to download View-Once media: %s", e)
        await ctx.reply(msg, f"⚠️ Detected View-Once {media_type}, but failed to decrypt: {e}")
        return

    if not data:
        return

    # Save to disk
    save_dir = _get_writable_dir(ctx)
    msg_id = getattr(raw.Info, "ID", "msg")[:8]
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"viewonce_{ts}_{msg_id}{ext}"
    filepath = os.path.join(save_dir, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(data)
        log.info("Saved View-Once %s to %s (%d bytes)", media_type, filepath, len(data))
    except Exception as e:
        log.warning("Could not write file %s: %s", filepath, e)

    sender_display = msg.sender_id.split("@")[0]
    size_kb = len(data) // 1024
    cap_text = f"\n💬 *Caption:* '{caption}'" if caption else ""

    alert = (
        "👁️ *[Anti-View-Once Unlocked]*\n"
        f"👤 *Sender:* @{sender_display}\n"
        f"📁 *Type:* {media_type} ({size_kb} KB){cap_text}\n"
        f"💾 *Saved to:* `{filepath}`\n"
        "🔓 *Permanent copy secured successfully!*"
    )
    await ctx.reply(msg, alert)

    # If it's an image, attempt re-send as a normal permanent photo
    if ext == ".jpg":
        try:
            await client.send_image(
                msg.chat, data,
                caption=f"🔓 Unlocked View-Once from @{sender_display}",
                viewonce=False
            )
        except Exception as e:
            log.debug("Direct re-send failed: %s", e)
