"""Create/list/verify private local backups. No remote APIs are contacted."""

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


async def run(args):
    from bot import config
    from bot.backups import read_backup
    from bot.maintenance import create_backup
    from bot.storage import HybridStorage

    if args.command == "list":
        for path in sorted(config.BACKUP_DIR.glob("kernel-backup-*.zip"), reverse=True):
            print(path)
    elif args.command == "verify":
        if not args.path:
            raise ValueError("Usage: manage_backups.py verify /path/to/backup.zip")
        snapshot, catalogs = read_backup(args.path, config.ROOT_DIR / "web" / "data")
        print(
            f"Valid backup: {len((snapshot or {}).get('jobs', []))} jobs, {len(catalogs)} catalog files"
        )
    else:
        if not Path(config.STORAGE_PATH).is_file():
            raise ValueError("No local state file exists yet")
        storage = HybridStorage(config.STORAGE_PATH, sync_mode="off")
        snapshot = await storage.snapshot()
        path = await asyncio.to_thread(
            create_backup,
            snapshot,
            config.BACKUP_DIR,
            config.ROOT_DIR / "web" / "data",
            config.BACKUP_KEEP,
        )
        print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "create", "verify"])
    parser.add_argument("path", nargs="?")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
