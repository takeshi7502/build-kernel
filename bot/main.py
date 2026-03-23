import os
# Xóa proxy để tránh lỗi kết nối trên môi trường Termux/VPS có proxy hệ thống
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("ALL_PROXY", None)
os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"

import aiohttp
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.request import HTTPXRequest

import config
from gki import build_gki_conversation, _del_msg_job, SUB_LEVELS
from permissions import is_owner, is_admin
import aiohttp.web as aiohttp_web
from web_sync import get_realtime_data

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING
)
logger = logging.getLogger("gww-bot")

DATA_JSON = "data.json"


class StorageBase:
    async def set_key(self, code: str, uses: int, vip: bool = False): ...
    async def get_uses(self, code: str) -> int: ...
    async def is_vip_key(self, code: str) -> bool: ...
    async def get_all_keys(self) -> Dict[str, Any]: ...
    async def consume(self, code: str) -> bool: ...
    async def delete_key(self, code: str) -> bool: ...
    async def add_job(self, job: Dict[str, Any]) -> Any: ...
    async def update_job(self, job_id, fields: Dict[str, Any]): ...
    async def list_unnotified_jobs(self) -> List[Dict[str, Any]]: ...
    async def list_user_active_jobs(self, user_id: int) -> List[Dict[str, Any]]: ...
    async def delete_old_messages(self, older_than_hours: int = 24): ...
    async def delete_old_jobs(self, older_than_days: int = 7): ...
    async def get_job_by_run_id(self, run_id: int) -> Optional[Dict[str, Any]]: ...
    async def track_message(self, message_id: int, chat_id: int, user_id: int): ...
    async def get_admin_ids(self) -> List[int]: ...
    async def add_admin(self, user_id: int): ...
    async def remove_admin(self, user_id: int) -> bool: ...
    async def delete_job_by_run_id(self, run_id: int) -> bool: ...


class JSONStorage(StorageBase):
    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()
        if not os.path.exists(self.path):
            self._save({"keys": {}, "jobs": [], "messages": {}})

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"keys": {}, "jobs": [], "messages": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Load JSON failed: %s", e)
            return {"keys": {}, "jobs": [], "messages": {}}

    def _save(self, data: Dict[str, Any]):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Save JSON failed: %s", e)

    async def set_key(self, code: str, uses: int, vip: bool = False):
        async with self._lock:
            data = self._load()
            data.setdefault("keys", {})[code] = {"uses": uses, "vip": vip}
            self._save(data)

    async def get_uses(self, code: str) -> int:
        async with self._lock:
            data = self._load()
            return int(data.get("keys", {}).get(code, {}).get("uses", 0))

    async def is_vip_key(self, code: str) -> bool:
        async with self._lock:
            data = self._load()
            return bool(data.get("keys", {}).get(code, {}).get("vip", False))

    async def get_all_keys(self) -> Dict[str, Any]:
        """Trả về dict {code: {"uses": N, "vip": bool}} cho tất cả key."""
        async with self._lock:
            data = self._load()
            keys = data.get("keys", {})
            return {code: {"uses": int(info.get("uses", 0)), "vip": bool(info.get("vip", False))}
                    for code, info in keys.items()}

    async def delete_key(self, code: str) -> bool:
        async with self._lock:
            data = self._load()
            if code in data.get("keys", {}):
                del data["keys"][code]
                self._save(data)
                return True
            return False

    async def consume(self, code: str) -> bool:
        async with self._lock:
            data = self._load()
            if code not in data.get("keys", {}):
                return False
            uses = int(data["keys"][code].get("uses", 0))
            if uses <= 0:
                return False
            data["keys"][code]["uses"] = uses - 1
            self._save(data)
            return True

    async def add_job(self, job: Dict[str, Any]) -> Any:
        async with self._lock:
            data = self._load()
            jobs = data.setdefault("jobs", [])
            job_id = max((j.get("_id", 0) for j in jobs), default=0) + 1
            job["_id"] = job_id
            job["created_at"] = datetime.now(timezone.utc).isoformat()
            jobs.append(job)
            self._save(data)
            return job_id

    async def update_job(self, job_id, fields: Dict[str, Any]):
        async with self._lock:
            data = self._load()
            for j in data.get("jobs", []):
                if j.get("_id") == job_id:
                    j.update(fields)
                    break
            self._save(data)

    async def get_jobs(self) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            return data.get("jobs", [])

    async def list_unnotified_jobs(self) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            return [j for j in data.get("jobs", []) if not j.get("notified")]

    async def list_user_active_jobs(self, user_id: int) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
            return [
                j for j in data.get("jobs", [])
                if j.get("user_id") == user_id
                and j.get("status") not in ("completed", "cancelled")
                and j.get("created_at", "") >= three_hours_ago
            ]

    async def delete_old_messages(self, older_than_hours: int = 24):
        async with self._lock:
            data = self._load()
            cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
            messages = data.get("messages", {})
            deleted = 0
            for msg_id, info in list(messages.items()):
                try:
                    ts = datetime.fromisoformat(info["timestamp"])
                    if ts < cutoff:
                        del messages[msg_id]
                        deleted += 1
                except Exception:
                    del messages[msg_id]
                    deleted += 1
            data["messages"] = messages
            self._save(data)
            return deleted

    async def delete_old_jobs(self, older_than_days: int = 7):
        async with self._lock:
            data = self._load()
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            jobs = data.get("jobs", [])
            new_jobs = []
            deleted = 0
            for j in jobs:
                try:
                    ts = datetime.fromisoformat(j.get("created_at", ""))
                    if ts >= cutoff:
                        new_jobs.append(j)
                    else:
                        deleted += 1
                except Exception:
                    deleted += 1
            data["jobs"] = new_jobs
            self._save(data)
            return deleted

    async def delete_job_by_run_id(self, run_id: int) -> bool:
        async with self._lock:
            data = self._load()
            jobs = data.get("jobs", [])
            new_jobs = [j for j in jobs if j.get("run_id") != run_id]
            if len(new_jobs) != len(jobs):
                data["jobs"] = new_jobs
                self._save(data)
                return True
            return False

    async def get_job_by_run_id(self, run_id: int) -> Optional[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            for j in data.get("jobs", []):
                if j.get("run_id") == run_id:
                    return j
            return None

    async def track_message(self, message_id: int, chat_id: int, user_id: int):
        async with self._lock:
            data = self._load()
            data.setdefault("messages", {})[str(message_id)] = {
                "chat_id": chat_id,
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            self._save(data)

    async def get_admin_ids(self) -> List[int]:
        async with self._lock:
            data = self._load()
            return data.get("admins", [])

    async def add_admin(self, user_id: int):
        async with self._lock:
            data = self._load()
            admins = data.setdefault("admins", [])
            if user_id not in admins:
                admins.append(user_id)
                self._save(data)

    async def remove_admin(self, user_id: int) -> bool:
        async with self._lock:
            data = self._load()
            admins = data.get("admins", [])
            if user_id in admins:
                admins.remove(user_id)
                data["admins"] = admins
                self._save(data)
                return True
            return False

    async def add_waiter(self, user_id: int, chat_id: int, user_name: str = ""):
        async with self._lock:
            data = self._load()
            waiters = data.setdefault("waiters", [])
            # Only add if not already waiting to avoid duplicates
            if not any(w.get("user_id") == user_id for w in waiters):
                waiters.append({"user_id": user_id, "chat_id": chat_id, "user_name": user_name})
                self._save(data)
                
    async def get_waiters(self) -> List[dict]:
        async with self._lock:
            data = self._load()
            return data.get("waiters", [])

    async def clear_waiters(self):
        async with self._lock:
            data = self._load()
            data["waiters"] = []
            self._save(data)

    async def add_successful_build(self, run_id: int, user_id: int, branch: str, user_name: str = ""):
        async with self._lock:
            data = self._load()
            builds = data.setdefault("successful_builds", [])
            build = {
                "run_id": run_id,
                "user_id": user_id,
                "user_name": user_name,
                "branch": branch,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            # Keep only the last 50 builds to prevent infinite growth
            builds.insert(0, build)
            data["successful_builds"] = builds[:50]
            self._save(data)
            
    async def get_successful_builds(self) -> List[dict]:
        async with self._lock:
            data = self._load()
            return data.get("successful_builds", [])

    def get_telegraph_token(self) -> Optional[str]:
        data = self._load()
        return data.get("telegraph_token")

    def set_telegraph_token(self, token: str):
        data = self._load()
        data["telegraph_token"] = token
        self._save(data)


class TelegraphAPI:
    """Tạo trang Telegraph chứa link tải artifacts."""
    BASE = "https://api.telegra.ph"

    def __init__(self, storage):
        self.storage = storage
        self._token = None

    async def _ensure_token(self):
        if self._token:
            return
        self._token = self.storage.get_telegraph_token()
        if self._token:
            return
        # Tạo tài khoản mới
        async with aiohttp.ClientSession() as s:
            resp = await s.post(f"{self.BASE}/createAccount", json={
                "short_name": "GKI Bot",
                "author_name": "GKI Build Bot"
            })
            data = await resp.json()
            if data.get("ok"):
                self._token = data["result"]["access_token"]
                self.storage.set_telegraph_token(self._token)

    @staticmethod
    def _format_build_config(config_inputs: Dict[str, Any]) -> str:
        if not isinstance(config_inputs, dict) or not config_inputs:
            return "Khong co du lieu cau hinh."

        # Keep the same order users see in confirmation, then append unknown keys.
        preferred_order = [
            "kernelsu_variant",
            "kernelsu_branch",
            "version",
            "use_zram",
            "use_bbg",
            "use_kpm",
            "cancel_susfs",
            "build_a12_5_10",
            "build_a13_5_15",
            "build_a14_6_1",
            "build_a15_6_6",
            "build_all",
            "release_type",
            "sub_levels",
        ]

        ordered_keys = [k for k in preferred_order if k in config_inputs]
        ordered_keys.extend(k for k in config_inputs.keys() if k not in ordered_keys)

        lines = []
        for key in ordered_keys:
            value = config_inputs.get(key, "")
            if isinstance(value, bool):
                value_text = "True" if value else "False"
            elif value is None:
                value_text = ""
            else:
                value_text = str(value)
            lines.append(f"- {key}: {value_text}")
        return "\n".join(lines)

    async def create_artifacts_page(self, title: str, artifacts: list, repo: str, run_id, owner: str, config_inputs: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Tạo trang Telegraph với danh sách artifacts. Trả về URL."""
        await self._ensure_token()
        if not self._token:
            return None

        # Xây dựng nội dung trang
        content = [
            {"tag": "h4", "children": ["Cau hinh build"]},
            {"tag": "pre", "children": [self._format_build_config(config_inputs or {})]},
            {"tag": "hr"},
            {"tag": "h4", "children": [f"📦 Danh sách file tải về"]},
            {"tag": "p", "children": [f"Build: {title}"]},
            {"tag": "hr"},
        ]
        for a in artifacts:
            name = a["name"]
            dl_url = f"https://nightly.link/{owner}/{repo}/actions/runs/{run_id}/{name}.zip"
            content.append({"tag": "p", "children": [
                {"tag": "a", "attrs": {"href": dl_url}, "children": [f"📥 {name}.zip"]}
            ]})
        
        content.append({"tag": "hr"})
        gh_url = f"https://github.com/{owner}/{repo}/actions/runs/{run_id}"
        content.append({"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": gh_url}, "children": ["🔗 Xem trên GitHub"]}
        ]})

        async with aiohttp.ClientSession() as s:
            resp = await s.post(f"{self.BASE}/createPage", json={
                "access_token": self._token,
                "title": title[:256],
                "author_name": "GKI Build Bot",
                "content": content,
                "return_content": False
            })
            data = await resp.json()
            if data.get("ok"):
                return data["result"]["url"]
        return None

class GitHubAPI:
    def __init__(self, token: str, owner: str):
        self.token = token
        self.owner = owner
        self.base = "https://api.github.com"
        self.session = None

    async def _get_session(self):
        if not self.session or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=60)
            resolver = aiohttp.AsyncResolver(nameservers=["8.8.8.8", "1.1.1.1"])
            connector = aiohttp.TCPConnector(resolver=resolver, limit=10)
            self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self.session

    async def _request(self, method: str, url: str, json_payload: Optional[dict] = None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "workflow-bot/1.2"
        }
        for attempt in range(3):
            try:
                sess = await self._get_session()
                async with sess.request(method, url, headers=headers, json=json_payload) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        logger.warning("Rate limited, retry after %s seconds", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 204:
                        return {"status": 204, "json": None}
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {"text": await resp.text()}
                    return {"status": resp.status, "json": data}
            except Exception as e:
                if attempt == 2:
                    logger.error("GitHub request failed: %s", e)
                    return {"status": 500, "json": {"error": str(e)}}
                await asyncio.sleep(2 ** attempt)
        return {"status": 500, "json": {}}

    async def dispatch_workflow(self, repo: str, workflow_file: str, ref: str, inputs: Dict[str, Any]):
        cleaned = {}
        for k, v in inputs.items():
            if v is None or str(v).strip() in ("", "none"):
                continue
            cleaned[k] = str(v).lower() if isinstance(v, bool) else str(v)
        str_inputs = {k: str(v) for k, v in cleaned.items()}
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/workflows/{workflow_file}/dispatches"
        payload = {"ref": ref, "inputs": str_inputs}
        return await self._request("POST", url, json_payload=payload)

    async def get_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
        return await self._request("GET", url)

    async def cancel_workflow_run(self, run_id: int):
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}/cancel"
        return await self._request("POST", url, {})

    async def list_artifacts_for_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}/artifacts"
        return await self._request("GET", url)

    async def list_runs_for_repo(self, repo: str, ref: str, created_iso: str):
        ts = datetime.fromisoformat(created_iso) - timedelta(seconds=10)
        created_filter = ts.isoformat()
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs?branch={ref}&per_page=10&created=%3E{created_filter}"
        return await self._request("GET", url)

    async def cancel_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}/cancel"
        return await self._request("POST", url)

    async def delete_run(self, repo: str, run_id: int):
        url = f"{self.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
        return await self._request("DELETE", url)



def tg_mention_html(user) -> str:
    name = user.first_name or user.username or "user"
    return f'<a href="tg://user?id={user.id}">{name}</a>'

async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def poller(app):
    gh: GitHubAPI = app.bot_data["gh"]
    storage: StorageBase = app.bot_data["storage"]

    async def cleanup_task():
        while True:
            try:
                deleted_msg = await storage.delete_old_messages(24)
                deleted_jobs = await storage.delete_old_jobs(7)
                if deleted_msg or deleted_jobs:
                    logger.info("Cleanup: %d messages, %d jobs", deleted_msg, deleted_jobs)
            except Exception as e:
                logger.error("Cleanup error: %s", e)
            await asyncio.sleep(3600)

    app.create_task(cleanup_task())

    while True:
        try:
            jobs = await storage.list_unnotified_jobs()
            for job in jobs:
                try:
                    if job.get("notify_via") == "userbot":
                        continue
                    repo = job["repo"]
                    run_id = job.get("run_id")
                    ref = job.get("ref", "main")
                    workflow_file = job.get("workflow_file")
                    job_created_at_iso = job.get("created_at")

                    if not run_id and ref and workflow_file and job_created_at_iso:
                        runs_resp = await gh.list_runs_for_repo(repo, ref, job_created_at_iso)
                        if runs_resp["status"] == 200:
                            possible_runs = []
                            job_created_dt = datetime.fromisoformat(job_created_at_iso)
                            for run in runs_resp["json"].get("workflow_runs", []):
                                if run.get("event") != "workflow_dispatch":
                                    continue
                                if workflow_file not in run.get("path", ""):
                                    continue
                                run_created_dt = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                                if run_created_dt >= (job_created_dt - timedelta(seconds=15)):
                                    possible_runs.append(run)
                            if possible_runs:
                                best_match = sorted(possible_runs, key=lambda x: x["created_at"])[0]
                                run_id = best_match["id"]
                                await storage.update_job(job["_id"], {"run_id": run_id})

                    if not run_id:
                        continue

                    rn = await gh.get_run(repo, int(run_id))
                    if rn["status"] != 200:
                        continue

                    status = rn["json"].get("status")
                    if status == "completed":
                        conclusion = rn["json"].get("conclusion")
                        html_url = rn["json"].get("html_url")

                        buttons = []
                        # Tạo trang Telegraph cho artifacts
                        telegraph: TelegraphAPI = app.bot_data.get("telegraph")
                        artifacts = await gh.list_artifacts_for_run(repo, int(run_id))
                        telegraph_url = None
                        if artifacts["status"] == 200:
                            arr = artifacts["json"].get("artifacts", [])
                            if arr and telegraph:
                                telegraph_url = await telegraph.create_artifacts_page(
                                    title=f"Build GKI #{run_id}",
                                    artifacts=arr, repo=repo,
                                    run_id=run_id,
                                    owner=config.GITHUB_OWNER,
                                    config_inputs=job.get("inputs", {})
                                )

                        if telegraph_url:
                            buttons.append([
                                InlineKeyboardButton("🌐 Xem GitHub", url=html_url),
                                InlineKeyboardButton("📦 Tải file", url=telegraph_url)
                            ])
                        else:
                            buttons.append([InlineKeyboardButton("🌐 Xem trên GitHub", url=html_url)])
                        
                        buttons.append([InlineKeyboardButton("📊 Web Dashboard", url="https://kernel.takeshi.dev/")])
                        kb = InlineKeyboardMarkup(buttons)

                        chat_id = job["chat_id"]
                        user_id = job["user_id"]
                        user_name = job.get("user_name", "")
                        if not user_name:
                            user_name = str(user_id)
                        mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                        icon = "✅" if conclusion == "success" else "❌" if conclusion == "failure" else "⚠️"
                        
                        created_at_dt = datetime.fromisoformat(job.get("created_at", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
                        elapsed = int((datetime.now(timezone.utc) - created_at_dt).total_seconds() // 60)

                        text = (
                            f"{icon} <b>Build {job.get('type','?').upper()} kết thúc!</b>\n"
                            f"📌 Trạng thái: <b>{conclusion.upper()}</b>\n"
                            f"⏱️ Thời gian: <b>{elapsed} phút</b>\n"
                            f"👤 Người gửi: {mention}"
                        )
                            
                        try:
                            msg = await app.bot.send_message(
                                chat_id=chat_id, text=text,
                                reply_markup=kb,
                                parse_mode=constants.ParseMode.HTML,
                                disable_web_page_preview=True
                            )
                            await storage.track_message(msg.message_id, chat_id, user_id)
                            await storage.update_job(job["_id"], {
                                "status": "completed",
                                "conclusion": conclusion,
                                "notified": True
                            })
                            if conclusion == "success":
                                await storage.add_successful_build(run_id, user_id, job.get("ref", "unknown"), user_name)
                                # Tự động gửi tin nhắn lưu cấu hình
                                try:
                                    await send_saved_config(app, run_id, job, user_id)
                                except Exception as e:
                                    logger.error("Auto PM save config failed: %s", e)
                        except Exception as e:
                            logger.error("Send notification failed: %s", e)
                            await storage.update_job(job["_id"], {"notified": True})

                        # Báo cho những người đang đợi
                        waiters = await storage.get_waiters()
                        if waiters:
                            for w in waiters:
                                w_user_id = w["user_id"]
                                w_chat_id = w["chat_id"]
                                w_name = w.get("user_name", str(w_user_id))
                                w_mention = f'<a href="tg://user?id={w_user_id}">{w_name}</a>'
                                msg_waiter = f"🔔 {w_mention} ơi, tiến trình đã hoàn tất! Bạn có thể dùng lệnh /gki lại ngay bây giờ nhé."
                                try:
                                    await app.bot.send_message(
                                        chat_id=w_chat_id, text=msg_waiter,
                                        parse_mode=constants.ParseMode.HTML
                                    )
                                except Exception:
                                    pass
                            await storage.clear_waiters()

                except Exception as e:
                    logger.error("Job %s error: %s", job.get("_id"), e, exc_info=True)
        except Exception as e:
            logger.error("Poller error: %s", e, exc_info=True)
            
        await asyncio.sleep(45)


async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if not is_owner(user.id):
        return
    try:
        parts = update.message.text.strip().split(maxsplit=2)
        if len(parts) != 3:
            raise ValueError()
        code = parts[1]
        action = parts[2].lower()
        
        if action == "delete":
            if await storage.delete_key(code):
                await update.message.reply_text(f"🗑️ Đã xoá key <code>{code}</code>.", parse_mode=constants.ParseMode.HTML)
            else:
                await update.message.reply_text(f"⚠️ Key <code>{code}</code> không tồn tại.", parse_mode=constants.ParseMode.HTML)
            return
        else:
            uses = int(action)
    except Exception:
        return await update.message.reply_text("Cú pháp: /key {mã} {số_lượt|delete}")
        
    await storage.set_key(code, uses, vip=False)
    await update.message.reply_text(f"✅ Đã set key <code>{code}</code> với {uses} lượt.", parse_mode=constants.ParseMode.HTML)


async def cmd_keyvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if not is_owner(user.id):
        return
    try:
        _, code, uses = update.message.text.strip().split(maxsplit=2)
        uses = int(uses)
    except Exception:
        return await update.message.reply_text("Cú pháp: /keyvip {mã} {số_lượt}")
    await storage.set_key(code, uses, vip=True)
    await update.message.reply_text(
        f"💎 Đã tạo VIP key <code>{code}</code> với {uses} lượt (không giới hạn 1h).",
        parse_mode=constants.ParseMode.HTML
    )


async def cmd_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if not is_owner(user.id):
        return
    keys = await storage.get_all_keys()
    if not keys:
        m = await update.message.reply_text("ℹ️ Chưa có key nào được tạo.")
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        return
    lines = ["🔑 <b>Danh sách Key</b>\n"]
    for i, (code, info) in enumerate(keys.items(), 1):
        uses = info["uses"]
        vip = info.get("vip", False)
        
        status = f"còn {uses} lượt" if uses > 0 else "Hết lượt"
        if vip:
            icon = "💎"
        elif uses > 0:
            icon = "✅"
        else:
            icon = "❌"
            
        lines.append(f"{i}. {icon}- <code>{code}</code> - {status}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{update.message.message_id}")]])
    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.HTML, reply_markup=kb)

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = await update.message.reply_text("🏓 Pong! Bot đang hoạt động bình thường.")
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=update.message.chat_id, data=update.message.message_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Xin chào! Mình là Bot Build Kernel GKI.\n\n"
        "🤖 Mình giúp tự động hóa quá trình cấu hình và biên dịch (build) Kernel Android (GKI) qua GitHub Actions.\n\n"
        "📌 <b>Các lệnh cơ bản:</b>\n"
        "• /gki - Bắt đầu quá trình chọn và build Kernel\n"
        "• /ping - Kiểm tra tình trạng hoạt động của Bot\n\n"
        "<i>Ghi chú: Bạn cần có cấu hình hợp lệ hoặc được Admin cấp quyền để sử dụng tính năng build.</i>"
    )
    if update.message:
        await update.message.reply_text(msg, parse_mode=constants.ParseMode.HTML)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    
    if not await is_admin(user.id, storage):
        return

    gh: GitHubAPI = context.application.bot_data["gh"]
    
    # Lấy thông tin run trực tiếp từ GitHub để luôn chính xác nhất
    active_runs = []
    for status in ["in_progress", "queued"]:
        url = f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs?status={status}&per_page=10"
        res = await gh._request("GET", url)
        if res.get("status") == 200:
            runs = res["json"].get("workflow_runs", [])
            for r in runs:
                # Lọc đúng branch
                if r.get("head_branch") == config.GKI_DEFAULT_BRANCH:
                    active_runs.append(r)

    if not active_runs:
        m = await update.message.reply_text("ℹ️ Hiện không có tiến trình build nào đang chạy.")
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
            if update.message:
                context.job_queue.run_once(_del_msg_job, when=60, chat_id=update.message.chat_id, data=update.message.message_id)
        return

    # Lấy danh sách jobs local để map user_id nếu có
    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}

    lines = []
    for idx, run in enumerate(active_runs, 1):
        run_id = run["id"]
        status = run["status"]
        name = run.get("name") or run.get("display_title") or "workflow"
        job = run_to_job.get(run_id, {})
        inputs = job.get("inputs", {})
        
        target_count = sum(1 for k in ('build_a12_5_10', 'build_a13_5_15', 'build_a14_6_1', 'build_a15_6_6') if inputs.get(k))
        if inputs.get("build_all"):
            target_count = 4
        target_count = max(1, target_count)
        
        expected_time_m = 18
        if target_count == 2:
            expected_time_m = 28
        elif target_count == 3:
            expected_time_m = 40
        elif target_count == 4:
            expected_time_m = 50
            
        expected_s = expected_time_m * 60
        
        created_at_str = run.get("created_at")
        elapsed_min = 0
        rem_m = expected_time_m
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                elapsed = datetime.now(timezone.utc) - created_at
                elapsed_min = int(elapsed.total_seconds() // 60)
                remaining_s = max(0, expected_s - elapsed.total_seconds())
                rem_m = int(remaining_s // 60)
            except:
                pass

        job = run_to_job.get(run_id, {})
        user_id = job.get("user_id", "Unknown")
        user_name = job.get("user_name", "Unknown")
        if user_id != "Unknown":
            mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
        else:
            mention = "GitHub / Manual"

        if idx > 1:
            lines.append("") # thêm dòng trống giữa các job
            
        lines.append(f"<b>{idx}. Task by {mention} ( #{run_id}) đang chạy</b>")
        lines.append(f"┠ <b>Đã chạy</b> {elapsed_min}p - <b>Ước tính còn</b> {rem_m}p")
        lines.append(f"┠ <b>Tình trạng:</b> {status} ({name[:20]})")
        lines.append(f"┖ <b>Huỷ job</b> → /cancel_{run_id}")

    msg_text = "\n".join(lines)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{update.message.message_id}")]])
    await update.message.reply_text(msg_text, parse_mode=constants.ParseMode.HTML, reply_markup=kb)

def _run_button_text(repo_label: str, run: dict) -> str:
    n = run.get("run_number")
    status = run.get("status")
    name = run.get("name") or run.get("display_title") or "workflow"
    return f"{repo_label} • #{n} • {status} • {name[:24]}"

async def show_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1, message_to_edit=None, cmd_msg_id=0):
    gh: GitHubAPI = context.application.bot_data["gh"]
    repo = config.GKI_REPO

    url = f"{gh.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs?status=completed&per_page=100"
    res = await gh._request("GET", url)
    if res.get("status") == 200:
        github_runs = res["json"].get("workflow_runs", [])
    else:
        github_runs = []

    workflow_files = list(config.GKI_WORKFLOWS.values())

    def _is_target_success_run(run: dict) -> bool:
        # GitHub list-runs endpoint có thể trả về nhiều workflow/conclusion,
        # nên lọc lại thủ công để khớp với "build GKI thành công".
        if run.get("status") != "completed":
            return False
        if run.get("conclusion") != "success":
            return False
        if run.get("head_branch") != config.GKI_DEFAULT_BRANCH:
            return False
        run_path = run.get("path", "")
        if workflow_files and not any(wf in run_path for wf in workflow_files):
            return False
        return True

    github_runs = [r for r in github_runs if _is_target_success_run(r)]

    if not github_runs:
        text = "ℹ️ Hiện không có lịch sử build GKI nào thành công."
        if message_to_edit:
            return await message_to_edit.edit_text(text)
        else:
            return await update.message.reply_text(text)

    items_per_page = 5
    total_pages = max(1, (len(github_runs) + items_per_page - 1) // items_per_page)
    if page < 1:
        page = total_pages
    if page > total_pages:
        page = 1

    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    current_builds = github_runs[start_idx:end_idx]

    storage: StorageBase = context.application.bot_data["storage"]
    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}

    if message_to_edit:
        await message_to_edit.edit_text("⏳ Đang tải thông tin trang...")
    else:
        message_to_edit = await update.message.reply_text("⏳ Đang tải thông tin trang...")

    text = f"🗂 <b>Danh sách các bản build GKI:</b>\n\n"

    # Render text for the 5 items
    for i, r in enumerate(current_builds):
        run_id = r["id"]
        branch = r.get("head_branch", "unknown")

        try:
            dt_obj = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            dt_local = dt_obj + timedelta(hours=7)
            time_str = dt_local.strftime("%H:%M %d/%m/%Y")
        except Exception:
            time_str = "Unknown"

        repo_url = f"https://github.com/{config.GITHUB_OWNER}/{repo}"
        html_url = r.get("html_url", f"{repo_url}/actions/runs/{run_id}")

        # Dùng nightly.link theo run_id để mở trang tải artifacts trực tiếp.
        # Cách này tránh gọi artifacts API cho từng item nên list load nhanh hơn.
        nightly_url = f"https://nightly.link/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
        artifact_str = f"📦 <a href='{nightly_url}'>Tải về</a>"

        job = run_to_job.get(run_id)
        if job:
            user_id = job.get("user_id")
            user_name = job.get("user_name", "")
            if not user_name:
                user_name = str(user_id)
            if user_id == 0:
                mention = "Hệ thống cũ"
            else:
                mention = f'{user_name}'
            inputs = job.get("inputs", {})
            build_lines = _format_build_lines(inputs)
        else:
            actor = r.get("actor", {}).get("login", "Unknown")
            mention = f"GitHub / {actor}"
            build_lines = []

        text += f"<b>{start_idx + i + 1}. Run #{run_id} by {mention}</b>\n"
        for bl in build_lines:
            text += f"{bl}\n"
        text += f"🕒 Time: {time_str} | 🔗 <a href='{html_url}'>GitHub</a> | {artifact_str}\n"
        text += f"📦 Xoá → /delete_{run_id}\n\n"

    kb = []
    if total_pages > 1:
        kb.append([
            InlineKeyboardButton("⬅️ Trước", callback_data=f"listpage:{page-1}"),
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="none"),
            InlineKeyboardButton("Sau ➡️", callback_data=f"listpage:{page+1}")
        ])
    kb.append([InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{cmd_msg_id}")])
    reply_markup = InlineKeyboardMarkup(kb)
    await message_to_edit.edit_text(text, parse_mode=constants.ParseMode.HTML, reply_markup=reply_markup, disable_web_page_preview=True)

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return
        
    await show_list_page(update, context, page=1, cmd_msg_id=update.message.message_id)

def _format_build_lines(inputs: dict) -> list[str]:
    lines = []
    if not inputs:
        return lines
        
    variant = inputs.get("variant")
    branch_raw = inputs.get("branch")
    branch = branch_raw.split("(")[0] if branch_raw else None
    c_name = inputs.get("custom_name", "")
    has_zram = "ZRAM" if inputs.get("use_zram") else ""
    has_bbg = "BBG" if inputs.get("use_bbg") else ""
    has_kpm = "KPM" if inputs.get("use_kpm") else ""
    has_susfs = "SUSFS" if not inputs.get("cancel_susfs") else ""
    ksu = inputs.get("ksu_type", "")
    
    parts = []
    for x in [variant, branch, c_name, has_zram, has_bbg, has_kpm, has_susfs, ksu]:
        if x:
            parts.append(str(x).strip())
            
    prefix = "|".join(parts)
    
    subs = inputs.get("sub_levels", "")
    sub_list = subs.split(",") if subs else []
    
    for tk, prefix_tk in [("build_a12_5_10", "A12-5.10"), ("build_a13_5_15", "A13-5.15"), 
                          ("build_a14_6_1", "A14-6.1"), ("build_a15_6_6", "A15-6.6")]:
        if inputs.get(tk):
            sl = sub_list if subs else SUB_LEVELS.get(tk, [])
            for s in sl:
                lines.append(f"🌿 Build: {prefix}|{prefix_tk}.{s.strip()}")
                
    return lines

async def cb_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return await q.answer()
    await q.answer()
    try:
        _, page_str = q.data.split(":")
        page = int(page_str)
    except:
        page = 1
    # Tìm cmd_msg_id từ nút Đóng trong reply_markup hiện tại
    cmd_msg_id = 0
    if q.message and q.message.reply_markup:
        for row in q.message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("closemsg:"):
                    try:
                        cmd_msg_id = int(btn.callback_data.split(":")[1])
                    except:
                        pass
    await show_list_page(update, context, page=page, message_to_edit=q.message, cmd_msg_id=cmd_msg_id)


async def cb_run_controls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return await q.answer()
    gh: GitHubAPI = context.application.bot_data["gh"]
    await q.answer()
    _, repo_tag, run_id_str = q.data.split(":", 2)
    run_id = int(run_id_str)
    repo = config.GKI_REPO
    view_url = f"https://github.com/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
    kb = [
        [InlineKeyboardButton("Hủy bỏ", callback_data=f"runctl:cancel:{repo_tag}:{run_id}"),
         InlineKeyboardButton("Đóng", callback_data=f"runctl:close:{repo_tag}:{run_id}")],
        [InlineKeyboardButton("Xem", url=view_url)]
    ]
    await q.edit_message_text(
        text=f"Run #{run_id} — GKI",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def cb_run_control_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return await q.answer()
    gh: GitHubAPI = context.application.bot_data["gh"]
    await q.answer()
    
    parts = q.data.split(":")
    action = parts[1]
    repo_tag = parts[2]
    run_id = int(parts[3])
    cmd_msg_id = None
    if len(parts) > 4 and parts[4].isdigit():
        cmd_msg_id = int(parts[4])

    repo = config.GKI_REPO
    if action == "cancel":
        # 1. Hiển thị trạng thái đang hủy
        await q.edit_message_text(f"⏳ Đang gửi lệnh hủy run #{run_id}...")
        
        # 2. Gửi lệnh hủy
        res = await gh.cancel_run(repo, run_id)
        if res["status"] not in (202, 204):
            await q.edit_message_text(f"❌ Gửi lệnh hủy thất bại: HTTP {res['status']}")
            return
        
        # 3. Cập nhật trạng thái đang chờ
        await q.edit_message_text(f"⏳ Đã gửi lệnh hủy run #{run_id}.\nĐang chờ xác nhận từ GitHub...")
        
        # 4. Poll cho đến khi run thực sự cancelled (tối đa 60s)
        import asyncio
        for i in range(20):  # 20 x 3s = 60s
            await asyncio.sleep(3)
            check = await gh.get_run(repo, run_id)
            if check.get("status") == 200:
                run_data = check.get("json", {})
                run_status = run_data.get("status", "")
                conclusion = run_data.get("conclusion", "")
                if run_status == "completed":
                    if conclusion == "cancelled":
                        # Tự động xoá job sau khi cancel
                        await gh.delete_run(repo, run_id)
                        await storage.delete_job_by_run_id(run_id)
                        await q.edit_message_text(
                            f"✅ <b>Đã hủy và xoá thành công!</b>\n\n"
                            f"Run #{run_id} đã được hủy và dọn dẹp.",
                            parse_mode="HTML"
                        )
                    else:
                        await q.edit_message_text(
                            f"ℹ️ Run #{run_id} đã hoàn tất với kết quả: <b>{conclusion}</b>",
                            parse_mode="HTML"
                        )
                    # Xóa tin nhắn lệnh gốc
                    if cmd_msg_id:
                        try:
                            await context.bot.delete_message(chat_id=q.message.chat_id, message_id=cmd_msg_id)
                        except Exception:
                            pass
                    # Tự xóa sau 60s
                    if context.job_queue:
                        context.job_queue.run_once(_del_msg_job, when=60, chat_id=q.message.chat_id, data=q.message.message_id)
                    return
        
        # Timeout - vẫn chưa cancelled sau 60s
        await q.edit_message_text(
            f"⚠️ Đã gửi lệnh hủy run #{run_id} nhưng chưa xác nhận được.\n"
            f"Vui lòng kiểm tra trên GitHub.",
            parse_mode="HTML"
        )
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=q.message.chat_id, data=q.message.message_id)
            
    elif action == "close":
        # Xóa cả tin nhắn bot và lệnh gọi
        chat_id = q.message.chat_id
        try:
            await q.delete_message()
        except Exception:
            pass
        # cmd_msg_id có thể đc truyền từ callback
        if cmd_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=cmd_msg_id)
            except Exception:
                pass

async def check_user_job_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if await is_admin(user.id, storage):
        return True

    # Check if a valid VIP key is provided
    if context.args:
        key = context.args[0]
        uses = await storage.get_uses(key)
        if uses > 0 and await storage.is_vip_key(key):
            return True  # Valid VIP key, bypass rate limit!

    active = await storage.list_user_active_jobs(user.id)
    if active:
        job_created_at = datetime.fromisoformat(active[0]["created_at"])
        elapsed = (datetime.now(timezone.utc) - job_created_at).total_seconds()
        if elapsed < 3600:
            remaining = int((3600 - elapsed) // 60) + 1
            m = await update.message.reply_text(f"⚠️ Chỉ được 1 job/1h. Vui lòng đợi {remaining} phút.")
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
            return False
    return True

async def cb_close_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return await q.answer()
    await q.answer()
    try:
        _, msg_id_str = q.data.split(":")
        msg_id = int(msg_id_str)
        if msg_id:
            await safe_delete_message(context, q.message.chat_id, msg_id)
        await q.message.delete()
    except Exception as e:
        logger.error("Close msg failed: %s", e)

async def send_saved_config(app, run_id, job, chat_id):
    inputs = job.get("inputs", {})
    if not inputs:
        return False
        
    lines = [f"💾 <b>LƯU TRỮ CẤU HÌNH GKI BUILD #{run_id}</b>"]
    # Get build date from job if available, else current time
    job_created_at = job.get("created_at")
    if job_created_at:
        try:
            dt = datetime.fromisoformat(job_created_at).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            lines.append(f"🕒 <b>Ngày build</b>: <code>{dt.strftime('%H:%M %d/%m/%Y')}</code>\n")
        except:
            pass
    else:
        lines.append(f"🕒 <b>Ngày build</b>: <code>Chưa rõ</code>\n")

    lines.append(f"• <b>KernelSU Variant</b>: <code>{inputs.get('kernelsu_variant', 'None')}</code>")
    lines.append(f"• <b>KernelSU Branch</b>: <code>{inputs.get('kernelsu_branch', 'None')}</code>")
    
    if inputs.get('version'):
        lines.append(f"• <b>Version Custom</b>: <code>{inputs.get('version')}</code>")

    lines.append(f"• <b>Compile BBG</b>: {'✅ Có' if inputs.get('use_bbg') else '❌ Không'}")
    lines.append(f"• <b>Compile KPM</b>: {'✅ Có' if inputs.get('use_kpm') else '❌ Không'}")
    lines.append(f"• <b>Dùng ZRAM</b>: {'✅ Có' if inputs.get('use_zram') else '❌ Không'}")
    # Cancel SUSFS logic is inverted from 'Bật SUSFS', check 'cancel_susfs'
    lines.append(f"• <b>Bật SUSFS</b>: {'❌ Không' if inputs.get('cancel_susfs') else '✅ Có'}")
    
    target_flags = []
    if inputs.get('build_a12_5_10'): target_flags.append('A12 (5.10)')
    if inputs.get('build_a13_5_15'): target_flags.append('A13 (5.15)')
    if inputs.get('build_a14_6_1'): target_flags.append('A14 (6.1)')
    if inputs.get('build_a15_6_6'): target_flags.append('A15 (6.6)')
    
    if inputs.get('build_all'):
        lines.append(f"• <b>Phiên bản Android</b>: <code>Tất cả (A12-A15)</code>")
    elif target_flags:
        lines.append(f"• <b>Phiên bản Android</b>: <code>{', '.join(target_flags)}</code>")
        
    sub_levels = inputs.get('sub_levels')
    if sub_levels:
        lines.append(f"• <b>Sub-versions (chỉ định)</b>: <code>{sub_levels.replace(',', ', ')}</code>")
    else:
        lines.append(f"• <b>Sub-versions</b>: <code>Tất cả các bản cập nhật phụ</code>")
        
    # Tạo Inline Keyboard cho tin nhắn lưu cấu hình
    save_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Xem trên GitHub", url=f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}"),
            InlineKeyboardButton("📦 Tải file", url=f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}")
        ]
    ])
    
    await app.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=save_kb,
        disable_web_page_preview=True
    )
    return True

async def cb_save_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return await q.answer()
    await q.answer()
    try:
        _, run_id_str = q.data.split(":")
        run_id = int(run_id_str)
    except:
        return await q.answer("Lỗi ID", show_alert=True)
        
    job = await storage.get_job_by_run_id(run_id)
    if not job:
        return await q.answer("Không tìm thấy dữ liệu build này trong hệ thống.", show_alert=True)
        
    try:
        if await send_saved_config(context.application, run_id, job, q.from_user.id):
            await q.answer("Đã gửi tin nhắn cấu hình vào chat riêng của bạn! 📩", show_alert=True)
        else:
            await q.answer("Không có thông tin cấu hình cho build này.", show_alert=True)
    except Exception as e:
        logger.error("Failed to PM user: %s", e)
        # Nút "Lưu" có thể được bấm trong nhóm. Nếu user chưa start bot, sẽ ném lỗi Forbidden
        await q.answer("❌ Lỗi: Bạn cần nhắn tin cho Bot trước (nhấn START) để nhận tin nhắn riêng.", show_alert=True)

async def cmd_cancel_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return
    
    text = update.message.text.strip()
    try:
        raw_cmd = text.split()[0]
        run_id_str = raw_cmd.split("_")[1].split("@")[0]
        run_id = int(run_id_str)
    except:
        return await update.message.reply_text("❌ ID không hợp lệ.")
        
    gh: GitHubAPI = context.application.bot_data["gh"]
    msg = await update.message.reply_text(f"⏳ Đang gửi lệnh hủy Run #{run_id} lên GitHub...")
    res = await gh.cancel_run(config.GKI_REPO, run_id)
    if res["status"] in (202, 204):
        await gh.delete_run(config.GKI_REPO, run_id)
        await storage.delete_job_by_run_id(run_id)
        await msg.edit_text(f"✅ Đã hủy và dọn dẹp thành công Run #{run_id}.")
    else:
        await msg.edit_text(f"❌ Lỗi hủy: {res['status']} {res.get('json', '')}")
        
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=10, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=10, chat_id=update.message.chat_id, data=update.message.message_id)

async def cmd_delete_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return
    
    text = update.message.text.strip()
    try:
        raw_cmd = text.split()[0]
        run_id_str = raw_cmd.split("_")[1].split("@")[0]
        run_id = int(run_id_str)
    except:
        return await update.message.reply_text("❌ ID không hợp lệ.")
        
    gh: GitHubAPI = context.application.bot_data["gh"]
    
    msg = await update.message.reply_text(f"⏳ Đang gửi lệnh xoá Run #{run_id} lên GitHub...")
    res = await gh.delete_run(config.GKI_REPO, run_id)
    
    if res["status"] in (202, 204):
        await msg.edit_text(f"✅ Đã yêu cầu xoá thành công Run #{run_id}.")
        await storage.delete_job_by_run_id(run_id)
    else:
        if res["status"] in (404,):
            await storage.delete_job_by_run_id(run_id)
            await msg.edit_text(f"✅ Run #{run_id} không tồn tại trên GitHub. Đã xoá khỏi dữ liệu nội bộ.")
        else:
            await msg.edit_text(f"❌ Lỗi xoá: {res['status']} {res.get('json', '')}")
            
    # Xoá tin nhắn sau 10s
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=10, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=10, chat_id=update.message.chat_id, data=update.message.message_id)

async def start_web_server(app_bot):
    """Khởi chạy API Web Server cục bộ phục vụ Dashboard Realtime"""
    app = aiohttp_web.Application()
    
    async def api_data(request):
        try:
            data = await get_realtime_data(app_bot)
            return aiohttp_web.json_response(data, headers={'Access-Control-Allow-Origin': '*'})
        except Exception as e:
            logger.error("Web API Error: %s", e)
            return aiohttp_web.json_response({"error": str(e)}, status=500)
            
    app.router.add_get('/api/data', api_data)
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app.router.add_static('/web', os.path.join(base_dir, 'web'))
    
    async def index(request):
        return aiohttp_web.FileResponse(os.path.join(base_dir, 'index.html'))
        
    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    
    port = int(os.getenv("WEB_PORT", 5000))
    site = aiohttp_web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Real-time Web Dashboard started natively on 0.0.0.0:{port}")


def main():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("Missing TELEGRAM_BOT_TOKEN")
        return

    storage = JSONStorage(DATA_JSON)
    gh = GitHubAPI(config.GITHUB_TOKEN, config.GITHUB_OWNER)
    telegraph = TelegraphAPI(storage)

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    app.bot_data["storage"] = storage
    app.bot_data["gh"] = gh
    app.bot_data["telegraph"] = telegraph

    # Background tasks sẽ được chạy trong _post_init

    # Owner-only commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("key", cmd_key, filters=filters.User(user_id=config.OWNER_ID)))
    app.add_handler(CommandHandler("keyvip", cmd_keyvip, filters=filters.User(user_id=config.OWNER_ID)))
    app.add_handler(CommandHandler("keys", cmd_keys, filters=filters.User(user_id=config.OWNER_ID)))

    # Admin commands (owner + static admins in .env)
    app.add_handler(CommandHandler("st", cmd_status))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CallbackQueryHandler(cb_list_page, pattern=r"^listpage:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_close_msg, pattern=r"^closemsg"))
    app.add_handler(CallbackQueryHandler(cb_run_controls, pattern=r"^run:gki:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_run_control_action, pattern=r"^runctl:(cancel|close):gki:\d+(?::\d+)?$"))
    app.add_handler(CallbackQueryHandler(cb_save_run, pattern=r"^saverun:\d+$"))
    app.add_handler(MessageHandler(filters.Regex(r"^/cancel_\d+(?:@[\w_]+)?$"), cmd_cancel_run))
    app.add_handler(MessageHandler(filters.Regex(r"^/delete_\d+(?:@[\w_]+)?$"), cmd_delete_run))

    # GKI conversation
    gki_conv = build_gki_conversation(gh, storage, config)

    async def gki_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_user_job_limit(update, context):
            return ConversationHandler.END
        return await gki_conv.entry_points[0].callback(update, context)

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("gki", gki_entry)],
        states=gki_conv.states,
        fallbacks=gki_conv.fallbacks,
        per_user=True,
        per_chat=False,
        conversation_timeout=300  # 5 phút timeout tránh conversation treo
    ))

    async def _post_init(app_):
        app_.create_task(poller(app_))
        app_.create_task(start_web_server(app_))
    app.post_init = _post_init

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()


