import os
import sys
import aiohttp
import logging
from dotenv import load_dotenv

# Load .env từ thư mục gốc (lùi lại 1 cấp so với file config.py)
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger("notify")

def _required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"[CONFIG] Thiếu biến môi trường bắt buộc: {key}")
        print(f"[CONFIG] Hãy copy .env.example thành .env và điền đầy đủ.")
        sys.exit(1)
    return val

# === Telegram ===
TELEGRAM_BOT_TOKEN: str = _required("TELEGRAM_BOT_TOKEN")

# === MongoDB (Optional) ===
MONGODB_URI: str = os.getenv("MONGODB_URI", "").strip()

# === GitHub ===
GITHUB_TOKEN: str = _required("GITHUB_TOKEN")
GITHUB_OWNER: str = _required("GITHUB_OWNER")
UPSTREAM_OWNER: str = os.getenv("UPSTREAM_OWNER", "zzh20188").strip()

# === GKI Repo ===
GKI_REPO: str = _required("GKI_REPO")
GKI_DEFAULT_BRANCH: str = os.getenv("GKI_DEFAULT_BRANCH", "main").strip()

# Parse GKI_WORKFLOWS: "Build=main.yml" (Mặc định nếu trong .env không ghi)
_wf_raw = os.getenv("GKI_WORKFLOWS", "Build=main.yml").strip()
GKI_WORKFLOWS: dict = {}
if _wf_raw:
    for pair in _wf_raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            name, file = pair.split("=", 1)
            GKI_WORKFLOWS[name.strip()] = file.strip()

if not GKI_WORKFLOWS:
    print("[CONFIG] GKI_WORKFLOWS trống hoặc sai format.")
    print("[CONFIG] Format: TenHienThi=workflow_file.yml,Ten2=file2.yml")
    sys.exit(1)

# === Permissions ===
OWNER_ID: int = int(_required("OWNER_ID"))

_admin_raw = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS: list = []
if _admin_raw:
    ADMIN_IDS = [int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()]

# === OKI Repo ===
OKI_REPO: str = os.getenv("OKI_REPO", "Action-Build").strip()
OKI_DEFAULT_BRANCH: str = os.getenv("OKI_DEFAULT_BRANCH", "SukiSU-Ultra").strip()
OKI_WORKFLOW: str = os.getenv("OKI_WORKFLOW", "Build Kernel OnePlus.yml").strip()

async def send_admin_notification(bot_token: str, owner_id: int, mention: str, view_url: str, job_type: str = ""):
    """Gửi thông báo có build mới tới Admin qua Telegram Bot API."""
    job_prefix = f" {job_type}" if job_type else ""
    admin_msg = (
        f"🚀 <b>Có build{job_prefix} mới từ {mention}!</b>\n"
        f"<blockquote><b>Xem : <a href='{view_url}'>Github</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>"
    )
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": owner_id, 
        "text": admin_msg, 
        "parse_mode": "HTML", 
        "disable_web_page_preview": True
    }
    
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=payload):
                pass
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)
