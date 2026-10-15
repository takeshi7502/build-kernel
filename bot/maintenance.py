"""Versioned, verified local backups and coalesced cloud snapshots."""

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .backups import read_backup
from .catalog import rebuild_downloads, update_download
from .workers import heartbeat

logger = logging.getLogger(__name__)


def create_backup(snapshot, directory, web_root, keep=14):
    directory = Path(directory)
    web_root = Path(web_root)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".backup-", suffix=".zip", dir=directory)
    os.close(descriptor)
    target = (
        directory
        / f"kernel-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.zip"
    )
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("data.json", json.dumps(snapshot, ensure_ascii=False))
            for path in sorted(web_root.rglob("*.json")):
                if path.is_file() and not path.is_symlink():
                    archive.write(path, "web_data/" + path.relative_to(web_root).as_posix())
        # Validate the exact bytes that will be restored, before retention removes
        # any previous backup. Do not include .env/Telegram/GitHub credentials.
        read_backup(temporary, web_root)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    backups = sorted(
        directory.glob("kernel-backup-*.zip"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for old in backups[max(2, keep) :]:
        old.unlink()
    return target


async def backup_worker(app):
    last_backup = 0
    while True:
        heartbeat(app, "backups")
        if time.monotonic() - last_backup >= config.BACKUP_INTERVAL_HOURS * 3600 or not last_backup:
            snapshot = await app.bot_data["storage"].snapshot()
            path = await asyncio.to_thread(
                create_backup,
                snapshot,
                config.BACKUP_DIR,
                config.ROOT_DIR / "web" / "data",
                config.BACKUP_KEEP,
            )
            app.bot_data["last_backup"] = time.time()
            last_backup = time.monotonic()
            logger.warning("Verified local backup created: %s", path.name)
        await asyncio.sleep(30)


async def cloud_worker(app):
    while True:
        heartbeat(app, "cloud-sync")
        await app.bot_data["storage"].flush_cloud()
        await asyncio.sleep(config.CLOUD_SYNC_INTERVAL)


async def cleanup_worker(app):
    next_cleanup = 0
    while True:
        heartbeat(app, "cleanup")
        if time.monotonic() >= next_cleanup:
            storage = app.bot_data["storage"]
            await storage.delete_old_messages(24)
            await storage.delete_old_jobs(30)
            await storage.prune_outbox()
            next_cleanup = time.monotonic() + 3600
        await asyncio.sleep(30)


async def catalog_worker(app):
    heartbeat(app, "catalog")
    await rebuild_downloads(app.bot_data["storage"], app)
    while True:
        heartbeat(app, "catalog")
        for job in await app.bot_data["storage"].get_jobs():
            if not job.get("catalog_pending"):
                continue
            heartbeat(app, "catalog")
            try:
                await update_download(job, job["run_id"], app)
                await app.bot_data["storage"].update_job(job["_id"], {"catalog_pending": False})
            except Exception:
                logger.exception("Catalog update will be retried for job %s", job["_id"])
        await asyncio.sleep(10)
