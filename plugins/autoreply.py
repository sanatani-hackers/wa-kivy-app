NAME = "autoreply"

# Common greeting keywords -> friendly response
GREETINGS = {"hi", "hello", "hey", "hlo", "namaste", "salam"}


async def on_message(ctx, msg):
    # Only auto-reply in direct chats or when explicitly greeted (avoid group spam)
    text = msg.text.strip().lower()
    if text in GREETINGS:
        sender_display = msg.sender_id.split("@")[0]
        reply_text = (
            f"Hello @{sender_display}! 👋\n"
            "I'm an automated assistant powered by WA Core running locally on Android.\n"
            "Type `!help` to see available commands."
        )
        await ctx.reply(msg, reply_text)
