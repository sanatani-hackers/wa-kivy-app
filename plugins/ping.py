NAME = "ping"


async def on_message(ctx, msg):
    text = msg.text.strip()
    if text.lower() == "!ping":
        await ctx.reply(msg, "🏓 Pong! WA Core bot is online & responsive.")
    elif text.lower() == "!help":
        help_text = (
            "🤖 *WA Core Bot Commands:*\n"
            "• `!ping` - Check bot status & latency\n"
            "• `!echo <text>` - Repeats your text\n"
            "• `!stats` - Shows active plugins & info\n"
            "• `!antidelete` - Anti-Delete & Anti-Edit status\n"
            "• Send `hi` or `hello` for auto-greeting"
        )
        await ctx.reply(msg, help_text)
