NAME = "ping"


async def on_message(ctx, msg):
    if msg.text.strip().lower() == "!ping":
        await ctx.reply(msg, "pong")
