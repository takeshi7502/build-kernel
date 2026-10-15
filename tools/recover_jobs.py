"""Recover missing local build records from dashboard catalogs (preview first)."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def collect_records(data_dir: Path, existing_jobs: list) -> list:
    known = {str(job.get("run_id")) for job in existing_jobs if job.get("run_id")}
    records = []
    for path in sorted(data_dir.glob("android*/*.json")):
        with path.open(encoding="utf-8") as source:
            catalog = json.load(source)
        for entry in catalog.get("entries", []):
            for variant, metadata in entry.get("downloads", {}).items():
                link = metadata.get("url", "") if isinstance(metadata, dict) else metadata
                if not isinstance(link, str):
                    continue
                parts = urlparse(link).path.strip("/").split("/")
                if len(parts) < 5 or parts[2:4] != ["actions", "runs"] or not parts[4].isdigit():
                    continue
                run_id = parts[4]
                if run_id in known:
                    continue
                known.add(run_id)
                records.append(
                    {
                        "type": "buildsave",
                        "status": "completed",
                        "conclusion": "success",
                        "notified": True,
                        "run_id": int(run_id),
                        "repo": parts[1],
                        "created_at": entry.get("date") or datetime.now(timezone.utc).isoformat(),
                        "user_name": entry.get("runner", "Recovery"),
                        "bs_variant": variant,
                        "bs_android": path.parent.name,
                        "bs_full_ver": entry.get("kernel", path.stem),
                        "inputs": {},
                    }
                )
    return records


async def recover(apply: bool):
    from bot import config
    from bot.storage import HybridStorage

    # Local recovery is not a competing writer to the MongoDB master snapshot.
    storage = HybridStorage(config.STORAGE_PATH, sync_mode="off")
    snapshot = await storage.snapshot()
    records = collect_records(ROOT_DIR / "web" / "data", snapshot.get("jobs", []))
    print(f"Found {len(records)} missing build records.")
    if not apply or not records:
        if records:
            print("Preview only. Stop the bot and rerun with --apply to restore; a .bak is saved.")
        return
    jobs = snapshot.setdefault("jobs", [])
    last_id = max(
        snapshot.get("last_job_id", 0), max((job.get("_id", 0) for job in jobs), default=0)
    )
    for record in records:
        last_id += 1
        record["_id"] = last_id
        jobs.append(record)
    snapshot["last_job_id"] = last_id
    await storage.restore_data(snapshot)
    print("Local storage updated. Restart only gki-bot to load the recovered records.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write recovery after stopping the bot; default is preview",
    )
    args = parser.parse_args()
    asyncio.run(recover(args.apply))


if __name__ == "__main__":
    main()
