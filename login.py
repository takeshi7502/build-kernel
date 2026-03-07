"""One-time script to create Telethon session. Delete after use."""
import asyncio
from telethon import TelegramClient

API_ID = 22958576
API_HASH = "0c625eaffc84b0429bbfdeb581acecc2"
SESSION = "gki_user"

async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start(phone="+84944117231")
    me = await client.get_me()
    print(f"OK! Logged in as @{me.username} (id={me.id})")
    await client.disconnect()

asyncio.run(main())
