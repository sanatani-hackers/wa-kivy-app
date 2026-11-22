import asyncio
import logging
import re

NAME = "remind"
log = logging.getLogger("plugin.remind")


def _parse_duration(s: str) -> int:
    """Parse string like '10s', '2m', '1h' into seconds."""
    s = s.strip().lower()
    match = re.match(r"^(\d+)([smh]?)$", s)
    if not match:
        return 0
    val, unit = int(match.group(1)), match.group(2)
    if unit == "m":
        return val * 60
    elif unit == "h":
        return val * 3600
    return val  # default seconds


async def on_message(ctx, msg):
    text = (msg.text or "").strip()
    if not text.lower().startswith("!remind"):
        return

    parts = text.split(maxsplit=2)
    # Usage: !remind 10s Text here
    if len(parts) < 3:
        usage = (
            "⏰ *Smart Reminder Bot Usage:*\n"
            "• `!remind 10s <task>` - Remind in 10 seconds\n"
            "• `!remind 5m <task>` - Remind in 5 minutes\n"
            "• `!remind 1h <task>` - Remind in 1 hour\n"
            "• Example: `!remind 15s Check boiling milk`"
        )
        await ctx.reply(msg, usage)
        return

    time_arg, task_text = parts[1], parts[2]
    secs = _parse_duration(time_arg)
    if secs <= 0 or secs > 86400:
        await ctx.reply(msg, "⚠️ Invalid time! Use e.g. `10s`, `2m`, `1h` (max 24h).")
        return

    sender_tag = msg.sender_id.split("@")[0]
    await ctx.reply(msg, f"⏳ Reminder set! I'll ping you in *{time_arg}* for: *'{task_text}'*")

    # Background delayed reminder
    async def _send_later():
        await asyncio.sleep(secs)
        alert = (
            "🔔 *[REMINDER ALERT]* 🔔\n"
            f"👤 @{sender_tag}\n"
            f"📌 *Task:* {task_text}\n"
            f"⏱️ *Scheduled for:* {time_arg} ago"
        )
        try:
            from common import send_android_notification
            send_android_notification(
                title=f"⏰ Reminder: {task_text}",
                text=f"Scheduled reminder for @{sender_tag}",
                notif_id=2003,
            )
        except Exception:
            pass
        await ctx.reply(msg, alert)

    asyncio.create_task(_send_later())
