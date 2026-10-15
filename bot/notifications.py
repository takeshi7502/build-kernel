"""Persistent per-chat Telegram deliveries with bounded retry and backoff."""

import asyncio
import logging
import time

from telegram.error import BadRequest, Forbidden, RetryAfter

from . import config
from .workers import heartbeat

logger = logging.getLogger(__name__)


async def deliver_once(app, kinds=None, worker_name="notifications"):
    storage = app.bot_data["storage"]
    records = await storage.due_notifications(kinds=kinds)
    for record in records:
        heartbeat(app, worker_name)
        if not await storage.notification_is_pending(record["id"]):
            continue
        try:
            if record.get("kind") == "broadcast":
                await app.bot_data["send_broadcast"](app.bot, record["chat_id"], record["payload"])
            else:
                try:
                    await app.bot.send_message(
                        chat_id=record["chat_id"],
                        text=record["text"],
                        message_thread_id=record.get("message_thread_id"),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except BadRequest as error:
                    if "topic_closed" not in str(error).lower() or not record.get(
                        "message_thread_id"
                    ):
                        raise
                    await app.bot.send_message(
                        chat_id=record["chat_id"],
                        text=record["text"],
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
        except RetryAfter as error:
            retry = error.retry_after
            retry = retry.total_seconds() if hasattr(retry, "total_seconds") else float(retry)
            await storage.finish_notification(
                record["id"],
                delay=retry + 1,
                error="RetryAfter",
                permanent=record["attempts"] + 1 >= config.NOTIFICATION_MAX_ATTEMPTS,
            )
        except (Forbidden, BadRequest) as error:
            # Blocked users, removed groups, missing messages and invalid markup
            # must not generate an endless retry loop.
            await storage.finish_notification(
                record["id"], permanent=True, error=type(error).__name__
            )
        except Exception as error:
            attempts = record.get("attempts", 0) + 1
            await storage.finish_notification(
                record["id"],
                permanent=attempts >= config.NOTIFICATION_MAX_ATTEMPTS,
                delay=min(3600, 5 * 2 ** min(attempts, 10)),
                error=type(error).__name__,
            )
            logger.warning("Notification %s failed (%s)", record["id"], type(error).__name__)
        else:
            await storage.finish_notification(record["id"], success=True)
    return bool(records)


async def notification_worker(app):
    while True:
        heartbeat(app, "notifications")
        busy = await deliver_once(app, kinds={"completion", "notice"})
        await asyncio.sleep(0.2 if busy else 2)


async def broadcast_worker(app):
    last_progress = 0
    while True:
        heartbeat(app, "broadcast")
        busy = await deliver_once(app, kinds={"broadcast"}, worker_name="broadcast")
        if time.monotonic() - last_progress >= 5:
            for batch in await app.bot_data["storage"].broadcast_progress():
                try:
                    await app.bot.edit_message_text(
                        chat_id=batch["progress_chat"],
                        message_id=batch["progress_message"],
                        text=f"{'✅' if batch['done'] else '⏳'} Broadcast: {batch['sent'] + batch['failed']}/{batch['total']}\nThành công: {batch['sent']} · Thất bại: {batch['failed']}",
                    )
                except BadRequest:
                    pass
                except Exception:
                    logger.warning("Broadcast progress update failed")
                    continue
                if batch["done"]:
                    await app.bot_data["storage"].finish_broadcast_progress(batch["id"])
            last_progress = time.monotonic()
        await asyncio.sleep(0.2 if busy else 2)
