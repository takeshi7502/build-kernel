import os
# Xoa proxy de tranh loi ket noi tren moi truong co proxy he thong
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("ALL_PROXY", None)
os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"

import asyncio
import json
import logging
import shlex
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from config import send_admin_notification
import config
from storage import HybridStorage

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gki-userbot")


# â”€â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


def _parse_int_list(raw: str) -> List[int]:
    out: List[int] = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        if x.lstrip("-").isdigit():
            out.append(int(x))
    return out


TELEGRAM_API_ID = int(_required("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = _required("TELEGRAM_API_HASH")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "gki_user").strip() or "gki_user"

GITHUB_TOKEN = _required("GITHUB_TOKEN")
GITHUB_OWNER = _required("GITHUB_OWNER")
BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
GKI_REPO = _required("GKI_REPO")
GKI_DEFAULT_BRANCH = os.getenv("GKI_DEFAULT_BRANCH", "main").strip() or "main"

_wf_raw = os.getenv("GKI_WORKFLOWS", "Build=main.yml").strip()
GKI_WORKFLOWS: Dict[str, str] = {}
for _pair in _wf_raw.split(","):
    _pair = _pair.strip()
    if "=" in _pair:
        _n, _f = _pair.split("=", 1)
        if _n.strip() and _f.strip():
            GKI_WORKFLOWS[_n.strip()] = _f.strip()
if not GKI_WORKFLOWS:
    GKI_WORKFLOWS = {"Build": "main.yml"}

WORKFLOW_FILE = list(GKI_WORKFLOWS.values())[0]
# ALLOWED_CHAT_IDS loaded dynamically from data.json via .auth command
_auth_chats: set = set()  # Populated at startup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON = os.getenv("USERBOT_DATA_FILE", os.path.join(BASE_DIR, "data.json")).strip() or os.path.join(BASE_DIR, "data.json")
OWNER_ID = int(os.getenv("OWNER_ID", "0").strip() or "0")
ADMIN_IDS = set(_parse_int_list(os.getenv("ADMIN_IDS", "")))
USERBOT_STANDALONE = os.getenv("USERBOT_STANDALONE", "0").strip().lower() in {"1", "true", "yes", "on"}

# â”€â”€â”€ Build data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
VARIANTS = ["SukiSU", "ReSukiSU", "Official", "Next", "MKSU"]
BRANCHES = ["Stable(æ ‡å‡†)", "Dev(å¼€å‘)"]
RELEASE_TYPES = ["Actions", "Pre-Release", "Release"]
BUILD_TARGETS = [
    ("Android 12 - 5.10", "build_a12_5_10"),
    ("Android 13 - 5.15", "build_a13_5_15"),
    ("Android 14 - 6.1", "build_a14_6_1"),
    ("Android 15 - 6.6", "build_a15_6_6"),
]
SUB_LEVELS = {
    "build_a12_5_10": ["66","81","101","110","117","136","149","160","168","177","185","198","205","209","218","226","233","236","237","240","246"],
    "build_a13_5_15": ["74","78","94","104","119","123","137","144","148","149","151","153","167","170","178","180","185","189","194"],
    "build_a14_6_1":  ["25","43","57","68","75","78","84","90","93","99","112","115","118","124","128","129","134","138","141","145","157"],
    "build_a15_6_6":  ["50","56","57","58","66","77","82","87","89","92","98","102","118"],
}

# Metadata per sub_level (os_patch_level, revision) â€” mirrors the kernel-aXX workflow matrix
SUB_LEVEL_META: Dict[str, Dict[str, tuple]] = {
    "build_a12_5_10": {
        "66":("2022-01","r11"),"81":("2022-03","r11"),"101":("2022-04","r28"),
        "110":("2022-07","r1"),"117":("2022-09","r1"),"136":("2022-11","r15"),
        "149":("2023-01","r1"),"160":("2023-03","r1"),"168":("2023-04","r9"),
        "177":("2023-07","r3"),"185":("2023-09","r1"),"198":("2024-01","r17"),
        "205":("2024-03","r1"),"209":("2024-05","r13"),"218":("2024-08","r14"),
        "226":("2024-11","r8"),"233":("2025-02","r1"),"236":("2025-05","r1"),
        "237":("2025-06","r1"),"240":("2025-09","r1"),"246":("2025-12","r1"),
    },
    "build_a13_5_15": {
        "74":("2023-01",""),"78":("2023-03",""),"94":("2023-05",""),
        "104":("2023-07",""),"119":("2023-09",""),"123":("2023-11",""),
        "137":("2024-01",""),"144":("2024-03",""),"148":("2024-05",""),
        "149":("2024-07",""),"151":("2024-08",""),"153":("2024-09",""),
        "167":("2024-11",""),"170":("2025-01",""),"178":("2025-03",""),
        "180":("2025-05",""),"185":("2025-07",""),"189":("2025-09",""),
        "194":("2025-12",""),
    },
    "build_a14_6_1": {
        "25":("2023-10",""),"43":("2023-11",""),"57":("2024-01",""),
        "68":("2024-03",""),"75":("2024-05",""),"78":("2024-06",""),
        "84":("2024-07",""),"90":("2024-08",""),"93":("2024-09",""),
        "99":("2024-10",""),"112":("2024-11",""),"115":("2024-12",""),
        "118":("2025-01",""),"124":("2025-02",""),"128":("2025-03",""),
        "129":("2025-04",""),"134":("2025-05",""),"138":("2025-06",""),
        "141":("2025-07",""),"145":("2025-09",""),"157":("2025-12",""),
    },
    "build_a15_6_6": {
        "50":("2024-06",""),"56":("2024-09",""),"57":("2024-10",""),
        "58":("2024-11",""),"66":("2025-01",""),"77":("2025-03",""),
        "82":("2025-04",""),"87":("2025-05",""),"89":("2025-06",""),
        "92":("2025-07",""),"98":("2025-09",""),"102":("2025-10",""),
        "118":("2025-12",""),
    },
}

# Maps bot target_key â†’ (android_version, kernel_version) as expected by kernel-custom.yml
TARGET_META: Dict[str, tuple] = {
    "build_a12_5_10": ("android12", "5.10"),
    "build_a13_5_15": ("android13", "5.15"),
    "build_a14_6_1":  ("android14", "6.1"),
    "build_a15_6_6":  ("android15", "6.6"),
}

CUSTOM_WORKFLOW = "kernel-custom.yml"  # Single-job clean dispatch target


TARGET_ALIASES = {
    "a12": "build_a12_5_10", "a13": "build_a13_5_15",
    "a14": "build_a14_6_1", "a15": "build_a15_6_6",
    "12": "build_a12_5_10", "13": "build_a13_5_15",
    "14": "build_a14_6_1", "15": "build_a15_6_6",
    "5.10": "build_a12_5_10", "5.15": "build_a13_5_15",
    "6.1": "build_a14_6_1", "6.6": "build_a15_6_6",
}
TARGET_KEYS = list(SUB_LEVELS.keys())

DEFAULT_INPUTS: Dict[str, Any] = {
    "kernelsu_variant": "SukiSU",
    "kernelsu_branch": "Stable(æ ‡å‡†)",
    "version": "",
    "use_zram": True,
    "use_bbg": True,
    "use_kpm": True,
    "cancel_susfs": False,
    "build_a12_5_10": False,
    "build_a13_5_15": False,
    "build_a14_6_1": True,
    "build_a15_6_6": False,
    "build_all": False,
    "release_type": "Actions",
    "sub_levels": "",
}


# â”€â”€â”€ HybridStorage â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


# â”€â”€â”€ GitHub API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class GitHubAPI:
    def __init__(self, token: str, owner: str):
        self.token = token
        self.owner = owner
        self.base = "https://api.github.com"
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=60)
            resolver = aiohttp.AsyncResolver(nameservers=["8.8.8.8", "1.1.1.1"])
            connector = aiohttp.TCPConnector(resolver=resolver, limit=10)
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method: str, url: str, json_payload: Optional[dict] = None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gki-userbot/1.0",
        }
        for attempt in range(3):
            try:
                sess = await self._get_session()
                async with sess.request(method, url, headers=headers, json=json_payload) as resp:
                    if resp.status == 204:
                        return {"status": 204, "json": None}
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {"text": await resp.text()}
                    return {"status": resp.status, "json": data}
            except Exception as exc:
                if attempt == 2:
                    return {"status": 500, "json": {"error": str(exc)}}
                await asyncio.sleep(2 ** attempt)
        return {"status": 500, "json": {}}

    async def dispatch_workflow(self, repo: str, workflow_file: str, ref: str, inputs: Dict[str, Any]):
        cleaned: Dict[str, str] = {}
        for key, value in inputs.items():
            if value is None:
                continue
            if isinstance(value, bool):
                cleaned[key] = str(value).lower()
            else:
                text = str(value).strip()
                if text in ("", "none"):
                    continue
                cleaned[key] = text
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/workflows/{workflow_file}/dispatches"
        payload = {"ref": ref, "inputs": cleaned}
        return await self._request("POST", url, json_payload=payload)

    async def list_runs(self, repo: str, per_page: int = 50, status: Optional[str] = None):
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/runs?per_page={per_page}"
        if status:
            url += f"&status={status}"
        return await self._request("GET", url)

    async def get_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/runs/{run_id}"
        return await self._request("GET", url)

    async def cancel_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/runs/{run_id}/cancel"
        return await self._request("POST", url)

    async def delete_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/runs/{run_id}"
        return await self._request("DELETE", url)

    async def list_runs_for_repo(self, repo: str, ref: str, created_iso: str):
        ts = datetime.fromisoformat(created_iso) - timedelta(seconds=10)
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/runs?branch={ref}&per_page=10&created=%3E{ts.isoformat()}"
        return await self._request("GET", url)

    async def list_artifacts_for_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{self.owner}/{repo}/actions/runs/{run_id}/artifacts"
        return await self._request("GET", url)


# â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _format_time_utc7(iso_time: str) -> str:
    try:
        dt_obj = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return (dt_obj + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    except Exception:
        return "Unknown"


def _is_allowed_chat(chat_id: Optional[int]) -> bool:
    if not _auth_chats:
        return False  # No groups authorized = block all
    return chat_id in _auth_chats


def _matches_target_run(run: dict) -> bool:
    # Accept all jobs in the GKI repo regardless of workflow file or branch
    return True




# â”€â”€â”€ GKI Flow State Machine â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Steps: variant -> branch -> version -> zram -> bbg -> kpm -> susfs -> target -> sub -> release -> confirm
STEPS = ["variant", "branch", "version", "zram", "bbg", "kpm", "susfs", "target", "sub", "release", "confirm"]

# Per-user session storage: key = (chat_id, sender_id)
_sessions: Dict[tuple, Dict[str, Any]] = {}


def _session_key(event) -> tuple:
    return (event.chat_id, event.sender_id)


def _get_session(key: tuple) -> Optional[Dict[str, Any]]:
    return _sessions.get(key)


def _clear_session(key: tuple):
    _sessions.pop(key, None)


def _new_session(key: tuple, build_key: Optional[str] = None, admin: bool = True, user_name: str = "Unknown", user_id: int = 0) -> Dict[str, Any]:
    session = {
        "step": "variant",
        "inputs": dict(DEFAULT_INPUTS),
        "selected_target": None,
        "selected_subs": set(),
        "menu_msg_id": None,
        "build_key": build_key,
        "admin": admin,
        "user_name": user_name,
        "user_id": user_id,
    }
    _sessions[key] = session
    return session


def _build_menu(title: str, options: List[str], back: bool = True, extra_footer: str = "") -> str:
    lines = [title, ""]
    for i, opt in enumerate(options, 1):
        lines.append(f"  {i}. {opt}")
    lines.append("")
    footer_parts = []
    if back:
        footer_parts.append("0 = Quay láº¡i")
    footer_parts.append("x = Há»§y")
    lines.append("  " + "  |  ".join(footer_parts))
    if extra_footer:
        lines.append(extra_footer)
    return "\n".join(lines)


def _build_sub_menu(session: Dict[str, Any]) -> str:
    target_key = session["selected_target"]
    available = SUB_LEVELS.get(target_key, [])
    selected = session["selected_subs"]
    target_label = next((label for label, k in BUILD_TARGETS if k == target_key), target_key)
    major = target_label.split(" - ")[-1] if " - " in target_label else ""

    lines = [f"<b>Chá»n sub-version cho {target_label}:</b>", f"<i>(ÄÃ£ chá»n: {len(selected)}/{len(available)})</i>", ""]
    for i, sv in enumerate(available, 1):
        icon = "âœ…" if sv in selected else "â¬œ"
        lines.append(f"  {i}. {icon} {major}.{sv}")
    lines.append("")
    lines.append("  a = Chá»n/bá» táº¥t cáº£")
    lines.append("  ok = Tiáº¿p tá»¥c (xÃ¡c nháº­n sub Ä‘Ã£ chá»n)")
    lines.append("  0 = Quay láº¡i  |  x = Há»§y")
    return "\n".join(lines)


def _build_confirm_text(inputs: Dict[str, Any]) -> str:
    lines = ["<b>XÃ¡c nháº­n build GKI:</b>", ""]
    for k, v in inputs.items():
        lines.append(f"  â€¢ {k}: {v}")
    lines.append("")
    lines.append("  1 = âœ… XÃ¡c nháº­n build")
    lines.append("  0 = Quay láº¡i  |  x = Há»§y")
    return "\n".join(lines)


# â”€â”€â”€ Init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
gh = GitHubAPI(GITHUB_TOKEN, GITHUB_OWNER)
storage = HybridStorage(
    DATA_JSON,
    config.MONGODB_URI,
    sync_mode=config.MONGODB_SYNC_MODE,
    writer_hostname=config.MONGODB_SYNC_WRITER_HOSTNAME,
)

# Use StringSession if available, else file-based session
_string_session = os.getenv("TELEGRAM_STRING_SESSION", "").strip()
if _string_session:
    client = TelegramClient(StringSession(_string_session), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    logger.info("Using StringSession (no file lock)")
else:
    client = TelegramClient(TELEGRAM_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    logger.info("Using file session: %s", TELEGRAM_SESSION)


# Own user ID (set at startup)
_my_id: int = 0

# Rate limit: track last build time per user
_user_last_build: Dict[int, float] = {}
RATE_LIMIT_SECONDS = 1800  # 30 minutes


def _is_admin(event) -> bool:
    """Check if sender is admin (self, owner, or in ADMIN_IDS)."""
    sid = event.sender_id
    if sid == _my_id:
        return True
    if OWNER_ID and sid == OWNER_ID:
        return True
    if sid in ADMIN_IDS:
        return True
    return False


def _is_authorized(event) -> bool:
    """Check if sender is any known user (admin or regular)."""
    # Everyone is authorized for public commands
    return True

# Track chats currently being processed (prevents re-entrant loop)
_processing_chats: set = set()


async def _safe_delete(event):
    """Silently delete a message."""
    try:
        await event.delete()
    except Exception:
        pass


async def _delete_later(msg, seconds: int = 10):
    """Schedule a message for deletion after N seconds."""
    async def _do():
        await asyncio.sleep(seconds)
        try:
            await msg.delete()
        except Exception:
            pass
    asyncio.ensure_future(_do())


async def _reply(event, text: str, html: bool = False):
    chat_id = event.chat_id
    kwargs = {
        "link_preview": False,
        "parse_mode": "html" if html else None
    }
    
    if hasattr(event.message, "reply_to") and event.message.reply_to and getattr(event.message.reply_to, "forum_topic", False):
        kwargs["reply_to"] = event.message.reply_to.reply_to_top_id or event.message.reply_to_msg_id

    _processing_chats.add(chat_id)
    try:
        msg = await client.send_message(
            chat_id, text, **kwargs
        )
        return msg
    finally:
        _processing_chats.discard(chat_id)


async def _reply_temp(event, text: str, seconds: int = 10, html: bool = False):
    """Reply and auto-delete after N seconds."""
    msg = await _reply(event, text, html=html)
    await _delete_later(msg, seconds)
    return msg


async def _update_menu(event, session: Dict[str, Any], text: str):
    """Edit menu message or send new one, tracking the message id."""
    chat_id = event.chat_id
    if session.get("menu_msg_id"):
        try:
            await client.edit_message(chat_id, session["menu_msg_id"], text, parse_mode="html")
            return
        except Exception:
            pass
    msg = await _reply(event, text, html=True)
    session["menu_msg_id"] = msg.id


def _task_header(session: Dict[str, Any]) -> str:
    uid = session.get("user_id", 0)
    name = session.get("user_name", "Unknown")
    return f'ðŸ“‹ <b>Task by <a href="tg://user?id={uid}">{name}</a></b>\n\n'


# â”€â”€â”€ Step handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def _show_step(event, session: Dict[str, Any]):
    """Display the current step's menu."""
    step = session["step"]
    admin = session.get("admin", True)
    header = _task_header(session)
    
    if step == "variant":
        text = header + _build_menu("<b>Chá»n KernelSU variant:</b>", VARIANTS, back=False)
    elif step == "branch":
        text = header + _build_menu("<b>Chá»n nhÃ¡nh KernelSU:</b>", ["Stable", "Dev"])
    elif step == "version":
        text = (header + "<b>Nháº­p tÃªn version</b>\n<i>(VD nháº­p: JinYan â†’ 5.10.209-JinYan)</i>\n"
                "Hoáº·c gá»­i <code>skip</code> Ä‘á»ƒ bá» qua.\n\n"
                "  0 = Quay láº¡i  |  x = Há»§y")
    elif step == "zram":
        text = header + _build_menu("<b>Báº­t ZRAM?</b> <i>(máº·c Ä‘á»‹nh: báº­t)</i>", ["âœ… Báº­t", "âŒ Táº¯t"])
    elif step == "bbg":
        text = header + _build_menu("<b>Báº­t BBG?</b> <i>(máº·c Ä‘á»‹nh: báº­t)</i>", ["âœ… Báº­t", "âŒ Táº¯t"])
    elif step == "kpm":
        text = header + _build_menu("<b>Báº­t KPM?</b> <i>(máº·c Ä‘á»‹nh: báº­t)</i>", ["âœ… Báº­t", "âŒ Táº¯t"])
    elif step == "susfs":
        text = header + _build_menu("<b>Táº¯t SUSFS?</b> <i>(máº·c Ä‘á»‹nh: báº­t)</i>", ["âœ… Táº¯t SUSFS", "âŒ Giá»¯ SUSFS"])
    elif step == "target":
        labels = [label for label, _ in BUILD_TARGETS]
        text = header + _build_menu("<b>Chá»n phiÃªn báº£n Android Ä‘á»ƒ build:</b>", labels)
    elif step == "sub":
        text = header + _build_sub_menu(session)
    elif step == "release":
        # Regular users skip release step (default Actions)
        if not admin:
            session["inputs"]["release_type"] = "Actions"
            session["step"] = "confirm"
            await _show_step(event, session)
            return
        text = header + _build_menu("<b>Chá»n loáº¡i release:</b>", RELEASE_TYPES)
    elif step == "confirm":
        text = header + _build_confirm_text(session["inputs"])
    else:
        text = "Lá»—i: step khÃ´ng xÃ¡c Ä‘á»‹nh."

    await _update_menu(event, session, text)


def _prev_step(step: str) -> Optional[str]:
    idx = STEPS.index(step)
    return STEPS[idx - 1] if idx > 0 else None


async def _handle_input(event, session: Dict[str, Any], raw: str) -> bool:
    """Process user input for current step. Returns True if session ended."""
    step = session["step"]
    val = raw.strip()
    sk = _session_key(event)

    # Cancel
    if val.lower() == "x":
        menu_msg_id = session.get("menu_msg_id")
        _clear_session(sk)
        # Delete menu message
        if menu_msg_id:
            try:
                await client.delete_messages(event.chat_id, menu_msg_id)
            except Exception:
                pass
        msg = await _reply(event, "âŒ ÄÃ£ há»§y phiÃªn.")
        await _delete_later(msg, 10)
        return True

    # Back
    if val == "0":
        prev = _prev_step(step)
        if prev:
            session["step"] = prev
            await _show_step(event, session)
        else:
            menu_msg_id = session.get("menu_msg_id")
            _clear_session(sk)
            if menu_msg_id:
                try:
                    await client.delete_messages(event.chat_id, menu_msg_id)
                except Exception:
                    pass
            msg = await _reply(event, "âŒ ÄÃ£ há»§y phiÃªn.")
            await _delete_later(msg, 10)
            return True
        return False

    inputs = session["inputs"]

    if step == "variant":
        try:
            idx = int(val) - 1
            if 0 <= idx < len(VARIANTS):
                inputs["kernelsu_variant"] = VARIANTS[idx]
                session["step"] = "branch"
                await _show_step(event, session)
                return False
        except ValueError:
            pass
        await _reply_temp(event, f"Chá»n tá»« 1-{len(VARIANTS)}.", 10)
        return False

    if step == "branch":
        try:
            idx = int(val) - 1
            if idx == 0:
                inputs["kernelsu_branch"] = "Stable(æ ‡å‡†)"
            elif idx == 1:
                inputs["kernelsu_branch"] = "Dev(å¼€å‘)"
            else:
                await _reply_temp(event, "Chá»n 1 hoáº·c 2.", 10)
                return False
            session["step"] = "version"
            await _show_step(event, session)
            return False
        except ValueError:
            pass
        await _reply(event, "Chá»n 1 hoáº·c 2.")
        return False

    if step == "version":
        if val.lower() in ("skip", "s", "none"):
            inputs["version"] = ""
        else:
            inputs["version"] = val if val.startswith("-") else f"-{val}"
        session["step"] = "zram"
        await _show_step(event, session)
        return False

    if step in ("zram", "bbg", "kpm", "susfs"):
        try:
            idx = int(val)
            if idx not in (1, 2):
                await _reply_temp(event, "Chá»n 1 hoáº·c 2.", 10)
                return False
            toggle_val = (idx == 1)
            if step == "zram":
                inputs["use_zram"] = toggle_val
            elif step == "bbg":
                inputs["use_bbg"] = toggle_val
            elif step == "kpm":
                inputs["use_kpm"] = toggle_val
            elif step == "susfs":
                inputs["cancel_susfs"] = toggle_val
            next_idx = STEPS.index(step) + 1
            session["step"] = STEPS[next_idx]
            await _show_step(event, session)
            return False
        except ValueError:
            pass
        await _reply_temp(event, "Chá»n 1 hoáº·c 2.", 10)
        return False

    if step == "target":
        try:
            idx = int(val) - 1
            if 0 <= idx < len(BUILD_TARGETS):
                _, key = BUILD_TARGETS[idx]
                for _, k in BUILD_TARGETS:
                    inputs[k] = (k == key)
                session["selected_target"] = key
                available = SUB_LEVELS.get(key, [])
                session["selected_subs"] = set()
                session["step"] = "sub"
                await _show_step(event, session)
                return False
        except ValueError:
            pass
        await _reply_temp(event, f"Chá»n tá»« 1-{len(BUILD_TARGETS)}.", 10)
        return False

    if step == "sub":
        target_key = session["selected_target"]
        available = SUB_LEVELS.get(target_key, [])
        selected = session["selected_subs"]

        is_admin = _is_admin(event)

        if val.lower() == "a":
            if not is_admin:
                await _reply_temp(event, "âš ï¸ User thÆ°á»ng chá»‰ Ä‘Æ°á»£c phÃ©p chá»n 1 sub-version!", 10)
                return False
            if len(selected) == len(available):
                selected.clear()
            else:
                selected.update(available)
            await _show_step(event, session)
            return False

        if val.lower() == "ok":
            if not selected:
                await _reply_temp(event, "âš ï¸ Chá»n Ã­t nháº¥t 1 sub-version!", 10)
                return False
            if len(selected) == len(available):
                inputs["sub_levels"] = ""
            else:
                inputs["sub_levels"] = ",".join(sorted(selected, key=lambda x: int(x)))
            session["step"] = "release"
            await _show_step(event, session)
            return False

        try:
            idx = int(val) - 1
            if 0 <= idx < len(available):
                sv = available[idx]
                if not is_admin:
                    selected.clear()
                    selected.add(sv)
                    inputs["sub_levels"] = sv
                    session["step"] = "release"
                    await _show_step(event, session)
                    return False
                    
                if sv in selected:
                    selected.discard(sv)
                else:
                    selected.add(sv)
                await _show_step(event, session)
                return False
        except ValueError:
            pass
        await _reply_temp(event, f"Chá»n tá»« 1-{len(available)}, 'a' (táº¥t cáº£), hoáº·c 'ok' (tiáº¿p tá»¥c).", 10)
        return False

    if step == "release":
        try:
            idx = int(val) - 1
            if 0 <= idx < len(RELEASE_TYPES):
                inputs["release_type"] = RELEASE_TYPES[idx]
                session["step"] = "confirm"
                await _show_step(event, session)
                return False
        except ValueError:
            pass
        await _reply_temp(event, f"Chá»n tá»« 1-{len(RELEASE_TYPES)}.", 10)
        return False

    if step == "confirm":
        if val == "1":
            return await _do_dispatch(event, session)
        await _reply_temp(event, "Gá»­i 1 Ä‘á»ƒ xÃ¡c nháº­n, 0 quay láº¡i, x há»§y.", 10)
        return False

    return False


async def _do_dispatch(event, session: Dict[str, Any]) -> bool:
    """Dispatch workflow and cleanup session. Uses single-message flow (edit menu msg)."""
    sk = _session_key(event)
    chat_id = event.chat_id
    sender_id = event.sender_id
    inputs = session["inputs"].copy()
    is_admin = _is_admin(event)
    menu_msg_id = session.get("menu_msg_id")

    # Rate limit for regular users (bypass if VIP key)
    if not is_admin:
        import time as _time
        build_key = session.get("build_key")
        is_vip = build_key and await storage.is_vip_key(build_key)
        if not is_vip:
            last = _user_last_build.get(sender_id, 0)
            elapsed = _time.time() - last
            if elapsed < RATE_LIMIT_SECONDS:
                remaining = int((RATE_LIMIT_SECONDS - elapsed) // 60)
                if menu_msg_id:
                    try:
                        await client.delete_messages(chat_id, menu_msg_id)
                    except Exception:
                        pass
                _clear_session(sk)
                await _reply_temp(event, f"âš ï¸ Giá»›i háº¡n 1 build/30 phÃºt. Vui lÃ²ng chá» ~{remaining} phÃºt.", 10)
                return True

    # Edit menu message to show progress
    if menu_msg_id:
        try:
            await client.edit_message(chat_id, menu_msg_id, "â³ Äang kiá»ƒm tra tráº¡ng thÃ¡i server...")
        except Exception:
            pass

    # Check concurrency
    active_runs_count = 0
    busy_run = None
    for st in ("in_progress", "queued"):
        res = await gh.list_runs(GKI_REPO, per_page=20, status=st)
        if res.get("status") == 200:
            runs = res.get("json", {}).get("workflow_runs", [])
            for r in runs:
                if _matches_target_run(r):
                    active_runs_count += 1
                    if not busy_run:
                        busy_run = r

    max_concurrent = 10
    if active_runs_count >= max_concurrent:
        eta_line = ""
        if busy_run and busy_run.get("created_at"):
            try:
                created_dt = datetime.fromisoformat(busy_run["created_at"].replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - created_dt).total_seconds()
                rem_m = max(0, int((2700 - elapsed) // 60))
                eta_line = f"\nâ€¢ Æ¯á»›c tÃ­nh hoÃ n táº¥t tiáº¿n trÃ¬nh cÅ© nháº¥t: ~{rem_m} phÃºt." if rem_m > 0 else "\nâ€¢ Æ¯á»›c tÃ­nh sáº¯p hoÃ n táº¥t."
            except Exception:
                pass
        msg = (f"âŒ MÃ¡y chá»§ Ä‘ang quÃ¡ táº£i!\n\n"
               f"â€¢ Hiá»‡n táº¡i Ä‘ang cÃ³ {active_runs_count} tiáº¿n trÃ¬nh.\n"
               f"â€¢ Vui lÃ²ng chá» rá»“i thá»­ láº¡i.{eta_line}")
        await storage.add_waiter(sender_id, chat_id, "")
        if menu_msg_id:
            try:
                await client.edit_message(chat_id, menu_msg_id, msg)
            except Exception:
                await _reply(event, msg)
        else:
            await _reply(event, msg)
        _clear_session(sk)
        return True

    # --- Smart dispatch: use kernel-custom.yml if exactly 1 target + 1 sub_level ---
    dispatch_file = WORKFLOW_FILE
    dispatch_inputs = inputs

    t_key = session.get("selected_target")
    sel_subs: set = session.get("selected_subs") or set()
    sub_levels_str = str(inputs.get("sub_levels", "")).strip()
    sub_list = [s.strip() for s in sub_levels_str.split(",") if s.strip()] if sub_levels_str else []

    # Use clean single-job workflow when: 1 target, exactly 1 specific sub chosen
    use_custom = (
        t_key in TARGET_META
        and not inputs.get("build_all")
        and len(sel_subs) == 1
        and len(sub_list) == 1
    )
    if use_custom:
        sl = sub_list[0]
        android_ver, kernel_ver = TARGET_META[t_key]
        meta = SUB_LEVEL_META.get(t_key, {}).get(sl, ("lts", ""))
        dispatch_file = CUSTOM_WORKFLOW
        dispatch_inputs = {
            "android_version":  android_ver,
            "kernel_version":   kernel_ver,
            "sub_level":        sl,
            "os_patch_level":   meta[0],
            "revision":         meta[1] if meta[1] else "r1",
            "kernelsu_variant": inputs.get("kernelsu_variant", "SukiSU"),
            "kernelsu_branch":  inputs.get("kernelsu_branch", "Stable(æ ‡å‡†)"),
            "version":          inputs.get("version", ""),
            "use_zram":         inputs.get("use_zram", False),
            "use_bbg":          inputs.get("use_bbg", False),
            "use_kpm":          inputs.get("use_kpm", False),
            "cancel_susfs":     inputs.get("cancel_susfs", False),
            "supp_op":          False,
        }

    logger.info("[dispatch] file=%s use_custom=%s t_key=%s", dispatch_file, use_custom, t_key)
    res = await gh.dispatch_workflow(
        repo=GKI_REPO,
        workflow_file=dispatch_file,
        ref=GKI_DEFAULT_BRANCH,
        inputs=dispatch_inputs,
    )
    if res.get("status") not in (201, 202, 204):
        err = f"âš ï¸ Dispatch failed: HTTP {res.get('status')} | {res.get('json')}"
        if menu_msg_id:
            try:
                await client.edit_message(chat_id, menu_msg_id, err)
            except Exception:
                await _reply(event, err)
        else:
            await _reply(event, err)
        _clear_session(sk)
        return True

    # Consume key for regular users (VIP keys are NOT consumed)
    build_key = session.get("build_key")
    if not is_admin and build_key:
        if not await storage.is_vip_key(build_key):
            await storage.consume(build_key)

    # Track rate limit (only for non-VIP users)
    if not is_admin:
        import time as _time
        build_key_for_rl = session.get("build_key")
        if not build_key_for_rl or not await storage.is_vip_key(build_key_for_rl):
            _user_last_build[sender_id] = _time.time()

    try:
        sender = await client.get_entity(sender_id)
        first = getattr(sender, 'first_name', '') or ''
        last = getattr(sender, 'last_name', '') or ''
        sender_name = f"{first} {last}".strip() or 'User'
    except Exception:
        sender_name = 'User'

    job = {
        "type": "gki",
        "repo": GKI_REPO,
        "workflow_file": dispatch_file,
        "ref": GKI_DEFAULT_BRANCH,
        "inputs": inputs,
        "user_id": sender_id,
        "user_name": sender_name,
        "chat_id": chat_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": None,
        "status": "dispatched",
        "conclusion": None,
        "notified": False,
        "notify_via": "userbot",
    }
    await storage.add_job(job)

    view_url = f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/workflows/{dispatch_file}"
    mention = f"<a href='tg://user?id={sender_id}'>{sender_name}</a>"
    success_text = (
        "âœ… <b>ÄÃ£ gá»­i build thÃ nh cÃ´ng!</b>\n"
        f"ðŸ‘¤ NgÆ°á»i gá»­i: {mention}\n\n"
        "<i>Tui sáº½ thÃ´ng bÃ¡o khi build hoÃ n táº¥t.</i>\n"
        f"<blockquote><b>Xem : <a href='{view_url}'>Github</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>"
    )
    # Edit menu message to show success (single message)
    if menu_msg_id:
        try:
            await client.edit_message(chat_id, menu_msg_id, success_text, parse_mode="html", link_preview=False)
        except Exception:
            await _reply(event, success_text, html=True)
    else:
        await _reply(event, success_text, html=True)
        
    if str(sender_id) != str(OWNER_ID):
        await send_admin_notification(BOT_TOKEN, int(OWNER_ID), mention, view_url)
            
    _clear_session(sk)
    return True



# â”€â”€â”€ Auth Commands â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@client.on(events.NewMessage(pattern=r"^\.auth$"))
async def auth_cmd(event):
    """Authorize this chat to use the userbot. Admin only."""
    if not _is_admin(event):
        return
    await _safe_delete(event)
    chat_id = event.chat_id
    if chat_id in _auth_chats:
        await _reply_temp(event, "â„¹ï¸ NhÃ³m nÃ y Ä‘Ã£ Ä‘Æ°á»£c auth rá»“i.", 10)
        return
    _auth_chats.add(chat_id)
    await storage.add_auth_chat(chat_id)
    await _reply_temp(event, f"âœ… ÄÃ£ auth nhÃ³m nÃ y (ID: <code>{chat_id}</code>).", 10, html=True)
    logger.info("Authorized chat: %s", chat_id)


@client.on(events.NewMessage(pattern=r"^\.ua$"))
async def unauth_cmd(event):
    """Remove authorization for this chat. Admin only."""
    if not _is_admin(event):
        return
    await _safe_delete(event)
    chat_id = event.chat_id
    if chat_id not in _auth_chats:
        await _reply_temp(event, "â„¹ï¸ NhÃ³m nÃ y chÆ°a Ä‘Æ°á»£c auth.", 10)
        return
    _auth_chats.discard(chat_id)
    await storage.remove_auth_chat(chat_id)
    await _reply_temp(event, f"âœ… ÄÃ£ huá»· auth nhÃ³m nÃ y (ID: <code>{chat_id}</code>).", 10, html=True)
    logger.info("Deauthorized chat: %s", chat_id)


# â”€â”€â”€ Commands â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@client.on(events.NewMessage(pattern=r"^\.ping$"))
async def ping_cmd(event):
    if not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    await _reply_temp(event, "ðŸ“ Pong! Userbot Ä‘ang hoáº¡t Ä‘á»™ng.", 10)



@client.on(events.NewMessage(pattern=r"^\.help$"))
async def help_cmd(event):
    if not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    is_admin = _is_admin(event)

    text = (
        "📖 <b>Danh sách lệnh Userbot (.help)</b>\n\n"
        "📌 <b>Ai cũng dùng được:</b>\n"
        ".gki &lt;key&gt; — Build GKI Kernel (cần key hợp lệ)\n"
        ".ping — Kiểm tra userbot hoạt động\n"
        ".help — Hiện hướng dẫn này\n"
    )

    if is_admin:
        text += (
            "\n🔒 <b>Chỉ Admin:</b>\n"
            ".gki — Build trực tiếp không cần key\n"
            ".st — Xem build đang chạy\n"
            ".list — Lịch sử build thành công\n"
            ".cancel &lt;run_id&gt; — Hủy build\n"
            ".delete &lt;run_id&gt; — Xóa run\n"
            ".key &lt;code&gt; &lt;uses&gt; — Tạo/sửa key\n"
            ".keyvip &lt;code&gt; &lt;uses&gt; — Tạo VIP key\n"
            ".keys — Xem danh sách key\n"
            ".auth — Cho phép bot hoạt động trong group này\n"
            ".ua — Xóa quyền hoạt động trong group khỏi bot\n"
        )

    await _reply_temp(event, text, 60, html=True)

@client.on(events.NewMessage(pattern=r"^\.st$"))
async def status_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    active_runs: List[dict] = []
    for st in ("in_progress", "queued"):
        res = await gh.list_runs(GKI_REPO, per_page=50, status=st)
        if res.get("status") == 200:
            runs = res.get("json", {}).get("workflow_runs", [])
            active_runs.extend([r for r in runs if _matches_target_run(r)])
    if not active_runs:
        await _reply_temp(event, "â„¹ï¸ KhÃ´ng cÃ³ build nÃ o Ä‘ang cháº¡y.", 10)
        return

    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}

    lines = ["<b>âš™ï¸ Build Ä‘ang cháº¡y:</b>"]
    for idx, run in enumerate(active_runs[:10], 1):
        run_id = run.get("id")
        run_name = run.get("name") or run.get("display_title") or "workflow"
        elapsed_m = 0
        created_at = run.get("created_at", "")
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                elapsed_m = int((datetime.now(timezone.utc) - created_dt).total_seconds() // 60)
            except Exception:
                pass
        job = run_to_job.get(run_id, {})
        user_name = job.get("user_name", "GitHub")
        url = run.get("html_url", f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}")
        lines.append(f"")
        lines.append(f"<b>{idx}. Run #{run_id}</b> | {run.get('status')} | {elapsed_m}p | by {user_name}")
        lines.append(f"   ðŸ”— <a href='{url}'>Xem trÃªn GitHub</a>")
        lines.append(f"   âŒ Há»§y: <code>.cancel {run_id}</code>")
    await _reply_temp(event, "\n".join(lines), 60, html=True)


@client.on(events.NewMessage(pattern=r"^\.keys$"))
async def keys_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    keys = await storage.get_all_keys()
    if not keys:
        await _reply_temp(event, "ChÆ°a cÃ³ key nÃ o.", 10)
        return
    lines = ["ðŸ”‘ <b>Danh sÃ¡ch Key</b>\n"]
    for i, (code, info) in enumerate(keys.items(), 1):
        uses = info["uses"]
        vip = info.get("vip", False)
        
        # 1. âœ…- loli - cÃ²n 9 lÆ°á»£t
        # 2. âŒ- test - Háº¿t lÆ°á»£t
        # 3. ðŸ’Ž- provipmax - cÃ²n 10 lÆ°á»£t
        
        status = f"cÃ²n {uses} lÆ°á»£t" if uses > 0 else "Háº¿t lÆ°á»£t"
        if vip:
            icon = "ðŸ’Ž"
        elif uses > 0:
            icon = "âœ…"
        else:
            icon = "âŒ"
            
        lines.append(f"{i}. {icon}- <code>{code}</code> - {status}")
    await _reply_temp(event, "\n".join(lines), 60, html=True)


@client.on(events.NewMessage(pattern=re.compile(r"^\.key\s+(\S+)\s+(\d+|delete)$", re.IGNORECASE)))
async def key_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    code = event.pattern_match.group(1)
    action = event.pattern_match.group(2).lower()
    
    if action == "delete":
        if await storage.delete_key(code):
            await _reply_temp(event, f"ðŸ—‘ï¸ ÄÃ£ xoÃ¡ key <code>{code}</code>.", 10, html=True)
        else:
            await _reply_temp(event, f"âš ï¸ Key <code>{code}</code> khÃ´ng tá»“n táº¡i.", 10, html=True)
    else:
        uses = int(action)
        await storage.set_key(code, uses, vip=False)
        await _reply_temp(event, f"âœ… ÄÃ£ set key <code>{code}</code> vá»›i {uses} lÆ°á»£t.", 10, html=True)


@client.on(events.NewMessage(pattern=r"^\.keyvip\s+(\S+)\s+(\d+)$"))
async def keyvip_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    code = event.pattern_match.group(1)
    uses = int(event.pattern_match.group(2))
    await storage.set_key(code, uses, vip=True)
    await _reply_temp(event, f"ðŸ’Ž ÄÃ£ táº¡o VIP key <code>{code}</code> vá»›i {uses} lÆ°á»£t (khÃ´ng giá»›i háº¡n 1h).", 10, html=True)


# Track list message IDs for reply-based pagination
_list_msg_ids: Dict[int, int] = {}  # msg_id -> page

@client.on(events.NewMessage(pattern=r"^\.list(?:\s+(\d+))?$"))
async def list_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)
    page = 1
    if event.pattern_match and event.pattern_match.group(1):
        page = max(1, int(event.pattern_match.group(1)))

    res = await gh.list_runs(GKI_REPO, per_page=100, status="completed")
    if res.get("status") != 200:
        await _reply_temp(event, f"List failed: HTTP {res.get('status')}", 10)
        return

    runs = [r for r in res.get("json", {}).get("workflow_runs", [])
            if _matches_target_run(r) and r.get("status") == "completed" and r.get("conclusion") == "success"]
    if not runs:
        await _reply_temp(event, "KhÃ´ng cÃ³ báº£n build thÃ nh cÃ´ng nÃ o.", 10)
        return

    per_page = 5
    total_pages = max(1, (len(runs) + per_page - 1) // per_page)
    if page > total_pages: page = total_pages
    start = (page - 1) * per_page
    chunk = runs[start:start + per_page]

    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}

    lines = [f"ðŸ—‚ <b>Danh sÃ¡ch build thÃ nh cÃ´ng</b> (trang {page}/{total_pages}):", ""]
    for idx, run in enumerate(chunk, start=start + 1):
        run_id = run["id"]
        time_str = _format_time_utc7(run.get("created_at", ""))
        gh_url = run.get("html_url", f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}")
        nightly_url = f"https://nightly.link/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}"
        job = run_to_job.get(run_id, {})
        user_id = job.get("user_id", 0)
        user_name = job.get("user_name", run.get("actor", {}).get("login", "Unknown"))
        if user_id:
            mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
        else:
            mention = user_name
        lines.append(f"<b>{idx}. Run #{run_id}</b> by {mention}")
        lines.append(f"Time: {time_str}")
        lines.append(f"XoÃ¡: <code>.delete {run_id}</code>")
        lines.append(f"<blockquote><b>Xem : <a href='{gh_url}'>Github</a> | <a href='{nightly_url}'>File</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>")
        lines.append("")
    msg = await _reply(event, "\n".join(lines), html=True)
    if msg:
        _list_msg_ids[msg.id] = page


# Pending action sessions (for numbered cancel/delete)
_pending_actions: Dict[tuple, Dict[str, Any]] = {}


async def _do_cancel_run(event, run_id: int):
    """Execute cancel + delete for a run."""
    msg = await _reply(event, f"â³ Äang gá»­i lá»‡nh há»§y run #{run_id}...")
    res = await gh.cancel_run(GKI_REPO, run_id)
    if res.get("status") not in (202, 204):
        result = await _reply(event, f"âŒ Cancel failed: HTTP {res.get('status')}")
        await _delete_later(result, 10)
        if msg: await _delete_later(msg, 10)
        return
    for _ in range(20):
        await asyncio.sleep(3)
        check = await gh.get_run(GKI_REPO, run_id)
        if check.get("status") == 200:
            run_data = check.get("json", {})
            if run_data.get("status") == "completed":
                conclusion = run_data.get("conclusion", "unknown")
                if conclusion == "cancelled":
                    await gh.delete_run(GKI_REPO, run_id)
                    await storage.delete_job_by_run_id(run_id)
                    result = await _reply(event, f"âœ… ÄÃ£ há»§y vÃ  xoÃ¡ thÃ nh cÃ´ng run #{run_id}.")
                else:
                    result = await _reply(event, f"Run #{run_id} Ä‘Ã£ káº¿t thÃºc: {conclusion}")
                await _delete_later(result, 10)
                if msg: await _delete_later(msg, 10)
                return
    result = await _reply(event, f"âš ï¸ ChÆ°a xÃ¡c nháº­n Ä‘Æ°á»£c tráº¡ng thÃ¡i run #{run_id}. Kiá»ƒm tra GitHub.")
    await _delete_later(result, 10)
    if msg: await _delete_later(msg, 10)


async def _do_delete_run(event, run_id: int):
    """Execute delete for a run."""
    res = await gh.delete_run(GKI_REPO, run_id)
    if res.get("status") in (202, 204):
        await storage.delete_job_by_run_id(run_id)
        await _reply_temp(event, f"âœ… ÄÃ£ xoÃ¡ run #{run_id}.", 10)
    elif res.get("status") == 404:
        await storage.delete_job_by_run_id(run_id)
        await _reply_temp(event, f"âœ… Run #{run_id} khÃ´ng tá»“n táº¡i trÃªn GitHub. ÄÃ£ xoÃ¡ khá»i data.", 10)
    else:
        await _reply_temp(event, f"âŒ Lá»—i xoÃ¡: HTTP {res.get('status')}", 10)


@client.on(events.NewMessage(pattern=r"^\.cancel(?:\s+(\d+))?$"))
async def cancel_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)

    # Direct cancel with run_id
    if event.pattern_match.group(1):
        run_id = int(event.pattern_match.group(1))
        await _do_cancel_run(event, run_id)
        return

    # Show numbered list of active runs
    active_runs: List[dict] = []
    for st in ("in_progress", "queued"):
        res = await gh.list_runs(GKI_REPO, per_page=50, status=st)
        if res.get("status") == 200:
            runs = res.get("json", {}).get("workflow_runs", [])
            active_runs.extend([r for r in runs if _matches_target_run(r)])
    if not active_runs:
        await _reply_temp(event, "â„¹ï¸ KhÃ´ng cÃ³ build nÃ o Ä‘ang cháº¡y.", 10)
        return

    lines = ["<b>âŒ Chá»n build Ä‘á»ƒ há»§y:</b>", ""]
    run_map = {}
    for idx, run in enumerate(active_runs[:10], 1):
        run_id = run.get("id")
        run_map[idx] = run_id
        elapsed_m = 0
        created_at = run.get("created_at", "")
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                elapsed_m = int((datetime.now(timezone.utc) - created_dt).total_seconds() // 60)
            except Exception:
                pass
        url = run.get("html_url", "")
        lines.append(f"  <b>{idx}.</b> Run #{run_id} | {run.get('status')} | {elapsed_m}p")
        lines.append(f"      ðŸ”— <a href='{url}'>GitHub</a>")
    lines.append("")
    lines.append("  x = Há»§y")
    await _reply_temp(event, "\n".join(lines), 60, html=True)
    _pending_actions[_session_key(event)] = {"type": "cancel", "map": run_map}


@client.on(events.NewMessage(pattern=r"^\.delete(?:\s+(\d+))?$"))
async def delete_cmd(event):
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    await _safe_delete(event)

    # Direct delete with run_id
    if event.pattern_match.group(1):
        run_id = int(event.pattern_match.group(1))
        await _do_delete_run(event, run_id)
        return

    # Show numbered list of completed runs
    res = await gh.list_runs(GKI_REPO, per_page=50, status="completed")
    if res.get("status") != 200:
        await _reply_temp(event, f"List failed: HTTP {res.get('status')}", 10)
        return
    completed_runs = [r for r in res.get("json", {}).get("workflow_runs", [])
                      if _matches_target_run(r) and r.get("status") == "completed"]
    if not completed_runs:
        await _reply_temp(event, "â„¹ï¸ KhÃ´ng cÃ³ build nÃ o Ä‘á»ƒ xoÃ¡.", 10)
        return

    lines = ["<b>ðŸ—‘ Chá»n build Ä‘á»ƒ xoÃ¡:</b>", ""]
    run_map = {}
    for idx, run in enumerate(completed_runs[:10], 1):
        run_id = run.get("id")
        run_map[idx] = run_id
        conclusion = run.get("conclusion", "unknown")
        icon = "âœ…" if conclusion == "success" else "âŒ" if conclusion == "failure" else "âš ï¸"
        time_str = _format_time_utc7(run.get("created_at", ""))
        lines.append(f"  <b>{idx}.</b> {icon} Run #{run_id} | {conclusion} | {time_str}")
    lines.append("")
    lines.append("  x = Há»§y")
    await _reply_temp(event, "\n".join(lines), 60, html=True)
    _pending_actions[_session_key(event)] = {"type": "delete", "map": run_map}


# â”€â”€â”€ .gki (interactive text-based flow) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@client.on(events.NewMessage(pattern=r"^\.gki(?:\s+(\S+))?$"))
async def gki_cmd(event):
    if not _is_allowed_chat(event.chat_id):
        return
    sk = _session_key(event)
    is_admin = _is_admin(event)

    user = await client.get_entity(event.sender_id)
    first_name = user.first_name if hasattr(user, "first_name") and user.first_name else ""
    last_name = user.last_name if hasattr(user, "last_name") and user.last_name else ""
    user_name = f"{first_name} {last_name}".strip() or "Unknown"

    # Regular users need a key
    if not is_admin:
        key = event.pattern_match.group(1) if event.pattern_match else None
        if not key:
            msg = await _reply(event, "âš ï¸ <b>Lá»—i:</b> Thiáº¿u Key!\nVD: <code>.gki &lt;key&gt;</code>", html=True)
            await _delete_later(msg, 10)
            await _delete_later(event, 10)
            return
        uses = await storage.get_uses(key)
        if uses <= 0:
            msg = await _reply(event, f"âŒ <b>Lá»—i:</b> Key <code>{key}</code> sai hoáº·c háº¿t lÆ°á»£t.", html=True)
            await _delete_later(msg, 10)
            await _delete_later(event, 10)
            return
        session = _new_session(sk, build_key=key, admin=False, user_name=user_name, user_id=user.id)
    else:
        session = _new_session(sk, admin=True, user_name=user_name, user_id=user.id)
        
    await _safe_delete(event)
    await _show_step(event, session)





# Valid user input pattern: numbers (1-99), commands (x, a, ok, skip, s, none), or short version names
import re
_VALID_INPUT_RE = re.compile(r'^(\d{1,2}|[xXaA]|ok|skip|s|none|[a-zA-Z0-9_.-]{1,30})$')


# â”€â”€â”€ List reply pagination handler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@client.on(events.NewMessage())
async def list_reply_handler(event):
    """Handle replies to list messages for pagination."""
    if not _is_admin(event) or not _is_allowed_chat(event.chat_id):
        return
    if not event.reply_to_msg_id:
        return
    # Check if it's a reply to a list message
    if event.reply_to_msg_id not in _list_msg_ids:
        return
    text = (event.raw_text or "").strip()
    if not text:
        return
    try:
        new_page = int(text)
    except ValueError:
        return
    await _safe_delete(event)
    # Fetch and display the new page
    res = await gh.list_runs(GKI_REPO, per_page=100, status="completed")
    if res.get("status") != 200:
        return
    all_runs = [r for r in res.get("json", {}).get("workflow_runs", [])
                if _matches_target_run(r) and r.get("conclusion") == "success"]
    if not all_runs:
        return
    per_page_count = 5
    total_pages = max(1, (len(all_runs) + per_page_count - 1) // per_page_count)
    new_page = max(1, min(new_page, total_pages))
    start = (new_page - 1) * per_page_count
    chunk = all_runs[start:start + per_page_count]
    if not chunk:
        return
    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}
    lines = [f"ðŸ—‚ <b>Danh sÃ¡ch build thÃ nh cÃ´ng</b> (trang {new_page}/{total_pages}):", ""]
    for idx, run in enumerate(chunk, start=start + 1):
        run_id = run["id"]
        time_str = _format_time_utc7(run.get("created_at", ""))
        gh_url = run.get("html_url", f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}")
        nightly_url = f"https://nightly.link/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}"
        job = run_to_job.get(run_id, {})
        user_id = job.get("user_id", 0)
        user_name = job.get("user_name", run.get("actor", {}).get("login", "Unknown"))
        if user_id:
            mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
        else:
            mention = user_name
        lines.append(f"<b>{idx}. Run #{run_id}</b> by {mention}")
        lines.append(f"Time: {time_str}")
        lines.append(f"XoÃ¡: <code>.delete {run_id}</code>")
        lines.append(f"<blockquote><b>Xem : <a href='{gh_url}'>Github</a> | <a href='{nightly_url}'>File</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>")
        lines.append("")
    # Edit original list message
    try:
        await client.edit_message(event.chat_id, event.reply_to_msg_id, "\n".join(lines), parse_mode="html", link_preview=False)
        _list_msg_ids[event.reply_to_msg_id] = new_page
    except Exception:
        pass


# â”€â”€â”€ Session input handler (catch reply during .gki flow + pending actions) â”€â”€â”€
@client.on(events.NewMessage())
async def session_input_handler(event):
    if not _is_allowed_chat(event.chat_id):
        return

    text = (event.raw_text or "").strip()
    if not text or text.startswith("."):
        return

    # For outgoing messages (from self): only process valid user input
    # This prevents bot replies/menus from being re-processed (infinite loop)
    if event.out:
        if '\n' in text or not _VALID_INPUT_RE.match(text):
            return
    else:
        # For incoming messages (from others): check authorization
        if not _is_authorized(event):
            return

    sk = _session_key(event)

    # Check pending cancel/delete action first
    pending = _pending_actions.get(sk)
    if pending:
        await _safe_delete(event)
        if text.lower() == "x":
            _pending_actions.pop(sk, None)
            await _reply_temp(event, "âŒ ÄÃ£ há»§y.", 10)
            return
        try:
            idx = int(text)
            run_map = pending.get("map", {})
            if idx in run_map:
                run_id = run_map[idx]
                _pending_actions.pop(sk, None)
                if pending["type"] == "cancel":
                    await _do_cancel_run(event, run_id)
                elif pending["type"] == "delete":
                    await _do_delete_run(event, run_id)
                return
            else:
                await _reply_temp(event, f"Chá»n tá»« 1-{len(run_map)}.", 10)
                return
        except ValueError:
            await _reply_temp(event, f"Gá»­i sá»‘ hoáº·c 'x' Ä‘á»ƒ há»§y.", 10)
            return

    # GKI session
    session = _get_session(sk)
    if not session:
        return
    await _safe_delete(event)
    await _handle_input(event, session, text)


# â”€â”€â”€ Poller (standalone mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def poller_loop():
    """Background task that polls GitHub for completed builds and notifies user."""
    while True:
        try:
            jobs = await storage.list_unnotified_jobs()
            for job in jobs:
                try:
                    if job.get("notify_via") != "userbot":
                        continue
                    repo = job.get("repo")
                    run_id = job.get("run_id")
                    ref = job.get("ref", "main")
                    workflow_file = job.get("workflow_file")
                    job_created_at_iso = job.get("created_at")

                    if not run_id and ref and workflow_file and job_created_at_iso:
                        runs_resp = await gh.list_runs_for_repo(repo, ref, job_created_at_iso)
                        if runs_resp.get("status") == 200:
                            possible = []
                            job_created_dt = datetime.fromisoformat(job_created_at_iso)
                            for run in runs_resp["json"].get("workflow_runs", []):
                                if run.get("event") != "workflow_dispatch":
                                    continue
                                if workflow_file not in run.get("path", ""):
                                    continue
                                run_dt = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                                if run_dt >= (job_created_dt - timedelta(seconds=15)):
                                    possible.append(run)
                            if possible:
                                best = sorted(possible, key=lambda x: x["created_at"])[0]
                                run_id = best["id"]
                                await storage.update_job(job["_id"], {"run_id": run_id})

                    if not run_id:
                        continue

                    rn = await gh.get_run(repo, int(run_id))
                    if rn.get("status") != 200:
                        continue

                    status = rn["json"].get("status")
                    if status == "completed":
                        conclusion = rn["json"].get("conclusion")
                        html_url = rn["json"].get("html_url")
                        nightly_url = f"https://nightly.link/{GITHUB_OWNER}/{repo}/actions/runs/{run_id}"

                        chat_id = job.get("chat_id")
                        user_id = job.get("user_id", 0)
                        user_name = job.get("user_name", "Unknown")
                        icon = "âœ…" if conclusion == "success" else "âŒ" if conclusion == "failure" else "âš ï¸"
                        mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'

                        created_at_dt = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
                        elapsed = int((datetime.now(timezone.utc) - created_at_dt).total_seconds() // 60)

                        if conclusion == "success":
                            text = (
                                f"{icon} <b>Build GKI hoÃ n táº¥t!</b>\n"
                                f"ðŸ‘¤ NgÆ°á»i nháº­n: {mention}\n"
                                f"â±ï¸ Thá»i gian: <b>{elapsed} phÃºt</b>\n"
                                f"ðŸ“Š Tráº¡ng thÃ¡i: <b>{conclusion.upper()}</b>\n"
                                f"<blockquote><b>Xem : <a href='{html_url}'>Github</a> | "
                                f"<a href='{nightly_url}'>File</a> | "
                                f"<a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>"
                            )
                        else:
                            text = (
                                f"{icon} <b>Build GKI tháº¥t báº¡i!</b>\n"
                                f"ðŸ‘¤ NgÆ°á»i gá»­i: {mention}\n"
                                f"â±ï¸ Thá»i gian: <b>{elapsed} phÃºt</b>\n"
                                f"ðŸ“Š Tráº¡ng thÃ¡i: <b>{conclusion.upper()}</b>\n"
                                f"ðŸ”— <a href='{html_url}'>Xem lá»—i trÃªn GitHub</a>"
                            )

                        try:
                            await client.send_message(chat_id, text, parse_mode="html", link_preview=False)
                        except Exception as e:
                            logger.error("Send notification failed: %s", e)

                        await storage.update_job(job["_id"], {
                            "status": "completed", "conclusion": conclusion, "notified": True
                        })
                        if conclusion == "success":
                            await storage.add_successful_build(run_id, job.get("user_id", 0), ref, user_name)

                        waiters = await storage.get_waiters()
                        if waiters:
                            for w in waiters:
                                try:
                                    await client.send_message(w["chat_id"],
                                        f"ðŸ”” Tiáº¿n trÃ¬nh Ä‘Ã£ hoÃ n táº¥t! Báº¡n cÃ³ thá»ƒ dÃ¹ng .gki láº¡i.", link_preview=False)
                                except Exception:
                                    pass
                            await storage.clear_waiters()

                except Exception as e:
                    logger.error("Poller job %s error: %s", job.get("_id"), e)
        except Exception as e:
            logger.error("Poller error: %s", e)
        await asyncio.sleep(45)


async def cleanup_loop():
    """Periodically clean up old data."""
    while True:
        try:
            await storage.delete_old_messages(24)
            await storage.delete_old_jobs(7)
        except Exception as e:
            logger.error("Cleanup error: %s", e)
        await asyncio.sleep(3600)


# â”€â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async def main():
    global _my_id, _auth_chats
    me = await client.get_me()
    _my_id = me.id

    # Load authorized chats from storage
    _auth_chats = await storage.get_auth_chats()

    logger.info("Userbot started as @%s (id=%s)", me.username, me.id)
    logger.info("Repo: %s/%s branch=%s workflow=%s", GITHUB_OWNER, GKI_REPO, GKI_DEFAULT_BRANCH, WORKFLOW_FILE)
    logger.info("Standalone mode: %s", USERBOT_STANDALONE)
    if _auth_chats:
        logger.info("Authorized chats: %s", sorted(_auth_chats))
    else:
        logger.info("No authorized chats. Use .auth in a group to authorize.")

    # Start background tasks
    asyncio.create_task(storage._sync_with_cloud())
    asyncio.create_task(poller_loop())
    if USERBOT_STANDALONE:
        asyncio.create_task(cleanup_loop())
        logger.info("Poller + cleanup started (standalone mode)")
    else:
        logger.info("Poller started (notifications via userbot)")

    try:
        await client.run_until_disconnected()
    finally:
        await gh.close()


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
