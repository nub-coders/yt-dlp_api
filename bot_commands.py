"""Shared Telegram bot command menu — used by both bot.py and main.py entrypoints."""


def setup_bot_commands(client):
    from pyrogram.types import BotCommand
    commands = [
        BotCommand("start", "🚀 Get your API token and welcome message"),
        BotCommand("menu", "📋 Show main menu with options"),
        BotCommand("ping", "🏓 Check bot latency"),
        BotCommand("status", "📊 Check your usage statistics"),
        BotCommand("token", "🔑 View your current API token"),
        BotCommand("revoke", "🔄 Revoke your current token"),
        BotCommand("help", "❓ Get help and API documentation"),
    ]
    try:
        client.set_bot_commands(commands)
        print("✅ Bot commands set successfully")
    except Exception as e:
        print(f"⚠️ Failed to set bot commands: {e}")
