NAME = "echo"


async def on_message(ctx, msg):
    text = msg.text.strip()
    if text.lower().startswith("!echo "):
        echo_content = text[6:].strip()
        if echo_content:
            await ctx.reply(msg, f"📢 {echo_content}")
        else:
            await ctx.reply(msg, "⚠️ Usage: `!echo <your message>`")
