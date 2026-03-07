"""Generate a StringSession for Telethon. Run once, copy the output to .env"""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = 22958576
API_HASH = "0c625eaffc84b0429bbfdeb581acecc2"

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\n\nCopy this string to TELEGRAM_STRING_SESSION in .env:\n")
    print(client.session.save())
    print()
