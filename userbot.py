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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from dotenv import load_dotenv
from telethon import TelegramClient, events

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gki-userbot")


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


def _parse_workflows(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, file_name = pair.split("=", 1)
        name = name.strip()
        file_name = file_name.strip()
        if name and file_name:
            out[name] = file_name
    return out


TELEGRAM_API_ID = int(_required("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = _required("TELEGRAM_API_HASH")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "gki_user").strip() or "gki_user"

GITHUB_TOKEN = _required("GITHUB_TOKEN")
GITHUB_OWNER = _required("GITHUB_OWNER")
GKI_REPO = _required("GKI_REPO")
GKI_DEFAULT_BRANCH = os.getenv("GKI_DEFAULT_BRANCH", "main").strip() or "main"
GKI_WORKFLOWS = _parse_workflows(os.getenv("GKI_WORKFLOWS", "Build=main.yml"))
if not GKI_WORKFLOWS:
    GKI_WORKFLOWS = {"Build": "main.yml"}

WORKFLOW_FILE = list(GKI_WORKFLOWS.values())[0]
ALLOWED_CHAT_IDS = set(_parse_int_list(os.getenv("USERBOT_ALLOWED_CHAT_IDS", "")))
DATA_JSON = os.getenv("USERBOT_DATA_FILE", "data.json").strip() or "data.json"

TARGET_KEYS = ["build_a12_5_10", "build_a13_5_15", "build_a14_6_1", "build_a15_6_6"]
TARGET_ALIASES = {
    "a12": "build_a12_5_10",
    "a13": "build_a13_5_15",
    "a14": "build_a14_6_1",
    "a15": "build_a15_6_6",
    "12": "build_a12_5_10",
    "13": "build_a13_5_15",
    "14": "build_a14_6_1",
    "15": "build_a15_6_6",
    "5.10": "build_a12_5_10",
    "5.15": "build_a13_5_15",
    "6.1": "build_a14_6_1",
    "6.6": "build_a15_6_6",
}

BOOL_TRUE = {"1", "true", "yes", "y", "on"}
BOOL_FALSE = {"0", "false", "no", "n", "off"}

DEFAULT_INPUTS: Dict[str, Any] = {
    "kernelsu_variant": "SukiSU",
    "kernelsu_branch": "Stable(标准)",
    "version": "",
    "build_time": "",
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


def _parse_bool(raw: str) -> bool:
    low = raw.strip().lower()
    if low in BOOL_TRUE:
        return True
    if low in BOOL_FALSE:
        return False
    raise ValueError(f"Invalid boolean value: {raw}")


def _normalize_branch(raw: str) -> str:
    low = raw.strip().lower()
    if low in {"stable", "std", "s"}:
        return "Stable(标准)"
    if low in {"dev", "d"}:
        return "Dev(开发)"
    return raw


def _normalize_release(raw: str) -> str:
    low = raw.strip().lower()
    if low in {"actions", "action"}:
        return "Actions"
    if low in {"pre-release", "prerelease", "pre"}:
        return "Pre-Release"
    if low in {"release", "rel"}:
        return "Release"
    raise ValueError(f"Invalid release type: {raw}")


def _normalize_target(raw: str) -> str:
    low = raw.strip().lower()
    if low in TARGET_KEYS:
        return low
    mapped = TARGET_ALIASES.get(low)
    if mapped:
        return mapped
    raise ValueError(f"Invalid target: {raw}")


def _build_inputs(arg_string: str) -> Tuple[Dict[str, Any], List[str]]:
    inputs = dict(DEFAULT_INPUTS)
    notes: List[str] = []

    if not arg_string or not arg_string.strip():
        return inputs, ["Dang dung cau hinh mac dinh (target: Android 14 - 6.1)."]

    pairs: Dict[str, str] = {}
    for token in shlex.split(arg_string):
        if "=" not in token:
            raise ValueError(f"Invalid argument '{token}'. Use key=value format.")
        key, value = token.split("=", 1)
        key = key.strip().lower()
        if not key:
            raise ValueError("Empty argument key.")
        pairs[key] = value.strip()

    if "variant" in pairs:
        inputs["kernelsu_variant"] = pairs["variant"]
    if "branch" in pairs:
        inputs["kernelsu_branch"] = _normalize_branch(pairs["branch"])
    if "version" in pairs:
        version = pairs["version"]
        if version and not version.startswith("-"):
            version = f"-{version}"
        inputs["version"] = version
    if "build_time" in pairs:
        inputs["build_time"] = pairs["build_time"]

    if "zram" in pairs:
        inputs["use_zram"] = _parse_bool(pairs["zram"])
    if "bbg" in pairs:
        inputs["use_bbg"] = _parse_bool(pairs["bbg"])
    if "kpm" in pairs:
        inputs["use_kpm"] = _parse_bool(pairs["kpm"])
    if "susfs" in pairs:
        inputs["cancel_susfs"] = _parse_bool(pairs["susfs"])

    if "target" in pairs:
        selected = _normalize_target(pairs["target"])
        for k in TARGET_KEYS:
            inputs[k] = (k == selected)

    if "subs" in pairs:
        raw_subs = pairs["subs"].strip()
        if raw_subs.lower() in {"", "all", "*"}:
            inputs["sub_levels"] = ""
        else:
            cleaned = [x.strip() for x in raw_subs.split(",") if x.strip()]
            if not cleaned:
                raise ValueError("subs cannot be empty when provided.")
            inputs["sub_levels"] = ",".join(cleaned)

    if "release" in pairs:
        inputs["release_type"] = _normalize_release(pairs["release"])

    unknown = sorted(set(pairs.keys()) - {
        "variant", "branch", "version", "build_time",
        "zram", "bbg", "kpm", "susfs", "target", "subs", "release",
    })
    if unknown:
        notes.append(f"Ignored unknown keys: {', '.join(unknown)}")

    return inputs, notes


def _matches_target_run(run: dict) -> bool:
    if run.get("head_branch") != GKI_DEFAULT_BRANCH:
        return False
    path = run.get("path", "")
    if WORKFLOW_FILE and WORKFLOW_FILE not in path:
        return False
    return True


def _format_time_utc7(iso_time: str) -> str:
    try:
        dt_obj = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return (dt_obj + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    except Exception:
        return "Unknown"


def _is_allowed_chat(chat_id: Optional[int]) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS

def _load_shared_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_JSON):
        return {"keys": {}, "jobs": [], "messages": {}}
    try:
        with open(DATA_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"keys": {}, "jobs": [], "messages": {}}

HELP_TEXT = (
    "GKI User Mode commands:\n"
    "/pings\n"
    "/sts\n/keyss\n"
    "/lists [page]\n"
    "/cancels <run_id>\n"
    "/gkis [key=value ...]\n\n"
    "Supported /gkis keys:\n"
    "variant=..., branch=stable|dev, version=..., build_time=...\n"
    "zram=true|false, bbg=true|false, kpm=true|false, susfs=true|false\n"
    "target=a12|a13|a14|a15 (hoac build_a12_5_10, ...)\n"
    "subs=all|66,81,101\n"
    "release=actions|pre-release|release\n\n"
    "Example:\n"
    "/gkis target=a13 variant=ReSukiSU version=HzzMonet release=actions subs=74,78"
)


gh = GitHubAPI(GITHUB_TOKEN, GITHUB_OWNER)
client = TelegramClient(TELEGRAM_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH)


async def _reply(event, text: str):
    await event.reply(text, link_preview=False)



@client.on(events.NewMessage(pattern=r"^/pings(?:@\w+)?$"))
async def ping_cmd(event):
    if not event.out or not _is_allowed_chat(event.chat_id):
        return
    await _reply(event, "Pong. User mode is running.")


@client.on(events.NewMessage(pattern=r"^/sts(?:@\w+)?$"))
async def status_cmd(event):
    if not event.out or not _is_allowed_chat(event.chat_id):
        return

    active_runs: List[dict] = []
    for st in ("in_progress", "queued"):
        res = await gh.list_runs(GKI_REPO, per_page=50, status=st)
        if res.get("status") == 200:
            runs = res.get("json", {}).get("workflow_runs", [])
            active_runs.extend([r for r in runs if _matches_target_run(r)])

    if not active_runs:
        await _reply(event, "Khong co build nao dang chay.")
        return

    lines = ["Build dang chay:"]
    for run in active_runs[:10]:
        run_id = run.get("id")
        run_name = run.get("name") or run.get("display_title") or "workflow"
        created_at = run.get("created_at", "")
        elapsed_m = 0
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                elapsed_m = int((datetime.now(timezone.utc) - created_dt).total_seconds() // 60)
            except Exception:
                elapsed_m = 0
        url = run.get("html_url", f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}")
        lines.append(
            f"- Run #{run_id} | {run.get('status')} | {elapsed_m}m | {run_name}\n  {url}"
        )

    await _reply(event, "\n".join(lines))



@client.on(events.NewMessage(pattern=r"^/keyss(?:@\w+)?$"))
async def keyss_cmd(event):
    if not event.out or not _is_allowed_chat(event.chat_id):
        return

    data = _load_shared_data()
    keys_obj = data.get("keys", {}) if isinstance(data, dict) else {}
    if not isinstance(keys_obj, dict) or not keys_obj:
        await _reply(event, "Chua co key nao trong data dung chung.")
        return

    lines = ["Danh sach key (shared data):"]
    for idx, (code, info) in enumerate(keys_obj.items(), start=1):
        uses = 0
        if isinstance(info, dict):
            try:
                uses = int(info.get("uses", 0))
            except Exception:
                uses = 0
        status = "con luot" if uses > 0 else "het luot"
        lines.append(f"{idx}. {code} -> {uses} ({status})")

    await _reply(event, "\n".join(lines))

@client.on(events.NewMessage(pattern=r"^/lists(?:@\w+)?(?:\s+(\d+))?$"))
async def list_cmd(event):
    if not event.out or not _is_allowed_chat(event.chat_id):
        return

    page = 1
    if event.pattern_match and event.pattern_match.group(1):
        page = max(1, int(event.pattern_match.group(1)))

    res = await gh.list_runs(GKI_REPO, per_page=100, status="completed")
    if res.get("status") != 200:
        await _reply(event, f"List failed: HTTP {res.get('status')}")
        return

    runs = res.get("json", {}).get("workflow_runs", [])
    runs = [
        r for r in runs
        if _matches_target_run(r)
        and r.get("status") == "completed"
        and r.get("conclusion") == "success"
    ]

    if not runs:
        await _reply(event, "Khong co ban build thanh cong nao.")
        return

    per_page = 5
    total_pages = max(1, (len(runs) + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    chunk = runs[start:start + per_page]

    lines = [f"Danh sach build thanh cong (trang {page}/{total_pages}):", ""]
    for idx, run in enumerate(chunk, start=start + 1):
        run_id = run["id"]
        actor = (run.get("actor") or {}).get("login", "Unknown")
        branch = run.get("head_branch", "unknown")
        time_str = _format_time_utc7(run.get("created_at", ""))
        gh_url = run.get("html_url", f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}")
        nightly_url = f"https://nightly.link/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}"
        lines.append(
            f"{idx}. Run #{run_id}\n"
            f"   Tu: {actor} | Nhanh: {branch}\n"
            f"   {time_str}\n"
            f"   GitHub: {gh_url}\n"
            f"   Tai file: {nightly_url}"
        )

    await _reply(event, "\n\n".join(lines))


@client.on(events.NewMessage(pattern=r"^/cancels(?:@\w+)?\s+(\d+)$"))
async def cancel_cmd(event):
    if not event.out or not _is_allowed_chat(event.chat_id):
        return

    run_id = int(event.pattern_match.group(1))
    res = await gh.cancel_run(GKI_REPO, run_id)
    if res.get("status") not in (202, 204):
        await _reply(event, f"Cancel failed for #{run_id}: HTTP {res.get('status')}")
        return

    await _reply(event, f"Da gui lenh huy run #{run_id}, dang cho xac nhan...")
    for _ in range(20):
        await asyncio.sleep(3)
        check = await gh.get_run(GKI_REPO, run_id)
        if check.get("status") == 200:
            run_data = check.get("json", {})
            if run_data.get("status") == "completed":
                conclusion = run_data.get("conclusion", "unknown")
                await _reply(event, f"Run #{run_id} da ket thuc voi ket qua: {conclusion}")
                return

    await _reply(event, f"Chua xac nhan duoc trang thai cua run #{run_id}. Kiem tra tren GitHub.")


@client.on(events.NewMessage(pattern=r"^/gkis(?:@\w+)?(?:\s+(.+))?$"))
async def gki_cmd(event):
    if not event.out or not _is_allowed_chat(event.chat_id):
        return

    arg_string = event.pattern_match.group(1) if event.pattern_match else ""
    try:
        inputs, notes = _build_inputs(arg_string or "")
    except ValueError as exc:
        await _reply(event, f"Input error: {exc}\n\n{HELP_TEXT}")
        return

    # Check concurrency to avoid action workflow overlap.
    busy_run: Optional[dict] = None
    for st in ("in_progress", "queued"):
        res = await gh.list_runs(GKI_REPO, per_page=50, status=st)
        if res.get("status") == 200:
            runs = res.get("json", {}).get("workflow_runs", [])
            for run in runs:
                if _matches_target_run(run):
                    busy_run = run
                    break
        if busy_run:
            break

    if busy_run:
        run_id = busy_run.get("id")
        run_url = busy_run.get("html_url", f"https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}")
        await _reply(
            event,
            f"Dang co tien trinh khac chay (run #{run_id}).\n"
            f"Vui long doi xong roi gui lai /gkis.\n{run_url}",
        )
        return

    res = await gh.dispatch_workflow(
        repo=GKI_REPO,
        workflow_file=WORKFLOW_FILE,
        ref=GKI_DEFAULT_BRANCH,
        inputs=inputs,
    )
    if res.get("status") not in (201, 202, 204):
        await _reply(event, f"Dispatch failed: HTTP {res.get('status')} | {res.get('json')}")
        return

    lines = [
        "Da gui build thanh cong.",
        f"Workflow: {WORKFLOW_FILE}",
        f"Repo: https://github.com/{GITHUB_OWNER}/{GKI_REPO}/actions/workflows/{WORKFLOW_FILE}",
    ]
    if notes:
        lines.append("Note: " + " | ".join(notes))
    await _reply(event, "\n".join(lines))


async def main():
    me = await client.get_me()
    logger.info("User mode started as @%s (id=%s)", me.username, me.id)
    logger.info("Repo target: %s/%s on branch %s", GITHUB_OWNER, GKI_REPO, GKI_DEFAULT_BRANCH)
    logger.info("Workflow: %s", WORKFLOW_FILE)
    if ALLOWED_CHAT_IDS:
        logger.info("Allowed chat ids: %s", sorted(ALLOWED_CHAT_IDS))

    try:
        await client.run_until_disconnected()
    finally:
        await gh.close()


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())