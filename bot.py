from pyrogram import Client, idle
from config import API_ID, API_HASH, BOT_TOKEN, GROUP, CHANNEL
from bot_commands import setup_bot_commands

telegram_app = Client(
    "ytdlp_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN, in_memory=True,
    plugins=dict(root="plugins"),
    device_model="Desktop",
    system_version="Windows 10",
    app_version="3.4.3 x64",
    lang_code="en",
    lang_pack="tdesktop"
)

def run_bot():
    telegram_app.start()
    setup_bot_commands(telegram_app)
    me = telegram_app.me
    print(f"🤖 Bot Started: @{me.username} ({me.first_name}) [ID: {me.id}]")
    idle()
    telegram_app.stop()

if __name__ == "__main__":
    run_bot()
