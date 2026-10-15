"""Persistent dashboard download catalog, independent of Telegram delivery."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from . import config
from .github import parse_utc as _parse_utc
from .gki import SUB_LEVEL_META, TARGET_META
from .storage import HybridStorage
from .workers import heartbeat

logger = logging.getLogger(__name__)


def load_downloads(version: str) -> dict:
    """Read legacy URL strings or metadata URLs, including X/LTS catalogs."""
    variants = {}
    for path in (config.ROOT_DIR / "web" / "data").glob("android*/*.json"):
        try:
            with path.open(encoding="utf-8") as source:
                data = json.load(source)
            downloads = {}
            for entry in data.get("entries", []):
                if str(entry.get("kernel", "")).strip() == version:
                    downloads.update(entry.get("downloads") or {})
            if version.upper() == f"{path.stem}.X".upper():
                downloads.update(data.get("lts_downloads") or {})
            for variant, metadata in downloads.items():
                url = metadata.get("url", "") if isinstance(metadata, dict) else metadata
                if variant and isinstance(url, str) and url:
                    variants[str(variant)] = url
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning("Cannot read download catalog %s: %s", path, exc)
    return variants


def _truthy_feature(value) -> bool:
    """Normalize workflow input values into booleans for web feature labels."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "enabled (开启)"}


def _gki_web_config_label(inputs: dict) -> str:
    """Build a compact config label shown after the KernelSU variant in the web modal."""
    parts = []
    if not _truthy_feature(inputs.get("cancel_susfs", True)):
        parts.append("SUSFS")
    if _truthy_feature(inputs.get("use_bbg")):
        parts.append("BBG")
    if _truthy_feature(inputs.get("use_zram")):
        parts.append("ZRAM")
    if _truthy_feature(inputs.get("use_kpm")):
        parts.append("KPM")
    if _truthy_feature(inputs.get("use_rekernel")):
        parts.append("Re-Kernel")
    droidspaces = str(inputs.get("droidspaces", "off") or "off").strip()
    if droidspaces and droidspaces.lower() != "off":
        parts.append(f"Droidspaces {droidspaces}")
    if _truthy_feature(inputs.get("supp_op")):
        parts.append("OP8E")
    return " + ".join(parts)


def _parse_job_created_at(job: dict):
    raw = str(job.get("created_at", "") or "").strip()
    if not raw:
        return None
    try:
        return _parse_utc(raw)
    except Exception:
        return None


def _catalog_inputs(job):
    """Normalize stored /gki and /build inputs for the shared web catalog."""
    inputs = dict(job.get("inputs") or {})
    if job.get("type") == "buildsave":
        target = next(
            (
                key
                for key, (android, kernel) in TARGET_META.items()
                if android == job.get("bs_android") and kernel == job.get("bs_kernel_ver")
            ),
            None,
        )
        if target:
            inputs[target] = True
            inputs["sub_levels"] = job.get("bs_sub_level", inputs.get("sub_level", ""))
    return inputs


def _iter_recent_web_catalog_gki_jobs(jobs, max_age_days: int = 90):
    """Yield recent successful default-version builds eligible for the catalog."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for job in jobs:
        if job.get("type") not in ("gki", "buildsave"):
            continue
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            continue
        if not job.get("run_id"):
            continue
        created_at = _parse_job_created_at(job)
        if not created_at or created_at < cutoff:
            continue
        inputs = _catalog_inputs(job)
        if str(inputs.get("version", "") or "").strip():
            continue
        target_keys = [key for key in TARGET_META if _truthy_feature(inputs.get(key))]
        sub_levels = [
            s.strip() for s in str(inputs.get("sub_levels", "") or "").split(",") if s.strip()
        ]
        if len(target_keys) != 1 or len(sub_levels) != 1:
            continue
        yield created_at, job


def _write_catalog_json(path, data):
    """Readers always see either the old or complete new JSON document."""
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def update_download(job: dict, run_id, app):
    """Use a successful default-version user /gki build as the web download source."""
    import json as _json

    inputs = _catalog_inputs(job)
    if str(inputs.get("version", "") or "").strip():
        logger.info("web catalog: skip GKI run %s because custom version is not default", run_id)
        return

    target_keys = [key for key in TARGET_META if _truthy_feature(inputs.get(key))]
    sub_levels_raw = str(inputs.get("sub_levels", "") or "").strip()
    sub_levels = [s.strip() for s in sub_levels_raw.split(",") if s.strip()]
    if len(target_keys) != 1 or len(sub_levels) != 1:
        logger.info(
            "web catalog: skip GKI run %s because it is not a single target/sub build: targets=%s subs=%s",
            run_id,
            target_keys,
            sub_levels,
        )
        return

    sub_level = sub_levels[0]
    target_key = target_keys[0]
    android, kernel_v = TARGET_META[target_key]
    full_ver = f"{kernel_v}.{sub_level}"
    variant = str(inputs.get("kernelsu_variant", "SukiSU") or "SukiSU").strip() or "SukiSU"
    nightly_link = (
        f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}"
    )

    json_path = os.path.join(config.ROOT_DIR, "web", "data", android, f"{kernel_v}.json")
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    else:
        data = {"entries": []}

    features = {
        "susfs": not _truthy_feature(inputs.get("cancel_susfs", True)),
        "bbg": _truthy_feature(inputs.get("use_bbg")),
        "zram": _truthy_feature(inputs.get("use_zram")),
        "kpm": _truthy_feature(inputs.get("use_kpm")),
        "rekernel": _truthy_feature(inputs.get("use_rekernel")),
        "droidspaces": str(inputs.get("droidspaces", "off") or "off"),
        "supp_op": _truthy_feature(inputs.get("supp_op")),
    }
    download_info = {
        "url": nightly_link,
        "config": _gki_web_config_label(inputs),
        "features": features,
        "source": "buildsave-auto" if job.get("type") == "buildsave" else "user-gki-auto",
        "run_id": int(run_id),
        "user_name": job.get("user_name", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if sub_level.upper() == "X":
        data.setdefault("lts_downloads", {})[variant] = download_info
        _write_catalog_json(json_path, data)
        return

    entries = data.setdefault("entries", [])
    target_entry = None
    for entry in entries:
        if entry.get("kernel") == full_ver:
            target_entry = entry
            break

    if target_entry is None:
        meta = SUB_LEVEL_META.get(target_key, {}).get(
            sub_level, (datetime.now(timezone.utc).strftime("%Y-%m"), "")
        )
        target_entry = {"date": meta[0], "kernel": full_ver}
        entries.append(target_entry)
        entries.sort(
            key=lambda item: [
                int(part) for part in str(item.get("kernel", "0.0.0")).split(".") if part.isdigit()
            ]
        )

    downloads = target_entry.setdefault("downloads", {})
    downloads[variant] = download_info

    _write_catalog_json(json_path, data)

    logger.warning(
        "web catalog: updated from user GKI %s %s %s -> %s",
        full_ver,
        variant,
        download_info.get("config") or "default",
        nightly_link,
    )


async def rebuild_downloads(storage: HybridStorage, app, max_age_days: int = 90):
    """Rebuild generated web downloads from all recent qualifying user /gki jobs."""
    try:
        jobs = await storage.get_jobs()
    except Exception as e:
        logger.error("web catalog: cannot load jobs for rebuild: %s", e)
        return

    eligible = sorted(
        _iter_recent_web_catalog_gki_jobs(jobs, max_age_days), key=lambda item: item[0]
    )
    updated = 0
    for _, job in eligible:
        if app:
            heartbeat(app, "catalog")
        try:
            await update_download(job, job.get("run_id"), app)
            updated += 1
        except Exception as e:
            logger.error(
                "web catalog: rebuild failed for job %s run %s: %s",
                job.get("_id"),
                job.get("run_id"),
                e,
            )

    logger.warning(
        "web catalog: refreshed recent builds; max_age_days=%s eligible=%s updated=%s",
        max_age_days,
        len(eligible),
        updated,
    )
