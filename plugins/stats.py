import time

NAME = "stats"
START_TIME = time.time()


async def on_message(ctx, msg):
    if msg.text.strip().lower() == "!stats":
        uptime_sec = int(time.time() - START_TIME)
        hrs, rem = divmod(uptime_sec, 3600)
        mins, secs = divmod(rem, 60)
        uptime_str = f"{hrs}h {mins}m {secs}s"
        
        active_plugins = ", ".join(ctx._core.plugins.names()) if hasattr(ctx, "_core") else "stats"
        queue_len = ctx._core.queue._q.qsize() if hasattr(ctx, "_core") and hasattr(ctx._core, "queue") else 0
        
        reply = (
            "📊 *WA Core Engine Stats:*\n"
            f"• ⏱️ *Uptime:* {uptime_str}\n"
            f"• 🧩 *Active Plugins:* `{active_plugins}`\n"
            f"• 📬 *Outbound Queue:* {queue_len} pending\n"
            "• 🟢 *Status:* Healthy"
        )
        await ctx.reply(msg, reply)
