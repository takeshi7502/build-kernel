import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"[CONFIG] Thiếu biến môi trường bắt buộc: {key}")
        print(f"[CONFIG] Hãy copy .env.example thành .env và điền đầy đủ.")
        sys.exit(1)
    return val


# === Telegram ===
TELEGRAM_BOT_TOKEN: str = _required("TELEGRAM_BOT_TOKEN")

# === GitHub ===
GITHUB_TOKEN: str = _required("GITHUB_TOKEN")
GITHUB_OWNER: str = _required("GITHUB_OWNER")

# === GKI Repo ===
GKI_REPO: str = _required("GKI_REPO")
GKI_DEFAULT_BRANCH: str = os.getenv("GKI_DEFAULT_BRANCH", "main").strip()

# Parse GKI_WORKFLOWS: "Build=build.yml,Release=test_release.yml" -> dict
_wf_raw = os.getenv("GKI_WORKFLOWS", "").strip()
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
