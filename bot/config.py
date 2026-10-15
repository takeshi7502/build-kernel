"""Validated runtime settings; environment variables override root .env."""

import os
import socket
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _required(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        raise SystemExit(f"[CONFIG] Thiếu {key}. Điền {ROOT_DIR / '.env'} (xem .env.example).")
    return value


def _integer(key: str, default=None, minimum: int = 1, maximum=None) -> int:
    raw = os.getenv(key, str(default) if default is not None else "").strip()
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"[CONFIG] {key} phải là số nguyên.") from None
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise SystemExit(f"[CONFIG] {key} phải trong khoảng {bounds}.")
    return value


def _workflows(raw: str) -> dict[str, str]:
    workflows = {}
    for pair in raw.split(","):
        name, separator, filename = pair.strip().partition("=")
        if not separator or not name.strip() or not filename.strip():
            raise SystemExit("[CONFIG] GKI_WORKFLOWS phải có dạng Build=main.yml,Other=other.yml.")
        workflows[name.strip()] = filename.strip()
    return workflows


# Telegram access: BotFather token only.
TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
OWNER_ID = _integer("OWNER_ID")
try:
    ADMIN_IDS = sorted(
        {int(value.strip()) for value in os.getenv("ADMIN_IDS", "").split(",") if value.strip()}
    )
    if any(value <= 0 for value in ADMIN_IDS):
        raise ValueError
except ValueError:
    raise SystemExit("[CONFIG] ADMIN_IDS phải là các ID dương, phân cách bằng dấu phẩy.") from None

# GitHub workflow repositories.
GITHUB_TOKEN = _required("GITHUB_TOKEN")
GITHUB_OWNER = _required("GITHUB_OWNER")
UPSTREAM_OWNER = os.getenv("UPSTREAM_OWNER", "zzh20188").strip()
GKI_REPO = _required("GKI_REPO")
GKI_DEFAULT_BRANCH = os.getenv("GKI_DEFAULT_BRANCH", "main").strip() or "main"
GKI_WORKFLOWS = _workflows(os.getenv("GKI_WORKFLOWS", "Build=main.yml").strip())
OKI_REPO = os.getenv("OKI_REPO", "Action-Build").strip()
OKI_DEFAULT_BRANCH = os.getenv("OKI_DEFAULT_BRANCH", "SukiSU-Ultra").strip()
OKI_WORKFLOW = os.getenv("OKI_WORKFLOW", "Build Kernel OnePlus.yml").strip()

# Local JSON is authoritative. Preserve the existing default during upgrades.
_storage_path = Path(os.getenv("STORAGE_PATH", "data.json").strip() or "data.json").expanduser()
STORAGE_PATH = str(
    (_storage_path if _storage_path.is_absolute() else ROOT_DIR / _storage_path).resolve()
)
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_SYNC_MODE = os.getenv("MONGODB_SYNC_MODE", "auto").strip().lower()
if MONGODB_SYNC_MODE not in {"auto", "push", "pull", "off"}:
    raise SystemExit("[CONFIG] MONGODB_SYNC_MODE phải là auto, push, pull hoặc off.")
MONGODB_SYNC_WRITER_HOSTNAME = os.getenv("MONGODB_SYNC_WRITER_HOSTNAME", "").strip().lower()
HOSTNAME = socket.gethostname().strip().lower()

# Nginx exposes HTTPS. Use WEB_HOST=0.0.0.0 for direct network access.
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
WEB_PORT = _integer("WEB_PORT", default=5000, maximum=65535)
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "").strip().rstrip("/")
if DASHBOARD_URL and not DASHBOARD_URL.startswith(("http://", "https://")):
    raise SystemExit("[CONFIG] DASHBOARD_URL phải bắt đầu bằng http:// hoặc https://.")

# Worker limits and local, versioned backups. Existing .env files need no edits.
MAX_ACTIVE_BUILDS = _integer("MAX_ACTIVE_BUILDS", default=10, maximum=50)
POLL_INTERVAL = _integer("POLL_INTERVAL", default=30, minimum=5, maximum=300)
WORKER_STALE_SECONDS = _integer("WORKER_STALE_SECONDS", default=600, minimum=60)
NOTIFICATION_MAX_ATTEMPTS = _integer("NOTIFICATION_MAX_ATTEMPTS", default=8, maximum=20)
CLOUD_SYNC_INTERVAL = _integer("CLOUD_SYNC_INTERVAL", default=30, minimum=5)
BACKUP_INTERVAL_HOURS = _integer("BACKUP_INTERVAL_HOURS", default=24)
BACKUP_KEEP = _integer("BACKUP_KEEP", default=14, minimum=2)
_backup_path = Path(os.getenv("BACKUP_DIR", "data/backups").strip() or "data/backups")
BACKUP_DIR = (_backup_path if _backup_path.is_absolute() else ROOT_DIR / _backup_path).resolve()
if BACKUP_DIR.is_relative_to((ROOT_DIR / "web").resolve()):
    raise SystemExit(
        "[CONFIG] BACKUP_DIR không được nằm trong web/ vì backup chứa dữ liệu riêng tư."
    )
