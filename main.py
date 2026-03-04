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
from typing import Dict, Any, Optional, List

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.request import HTTPXRequest

import config
from gki import build_gki_conversation, _del_msg_job
from permissions import is_owner, is_admin

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING
)
logger = logging.getLogger("gww-bot")

DATA_JSON = "data.json"


class StorageBase:
    async def set_key(self, code: str, uses: int): ...
    async def get_uses(self, code: str) -> int: ...
    async def consume(self, code: str) -> bool: ...
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

    async def set_key(self, code: str, uses: int):
        async with self._lock:
            data = self._load()
            data.setdefault("keys", {})[code] = {"uses": uses}
            self._save(data)

    async def get_uses(self, code: str) -> int:
        async with self._lock:
            data = self._load()
            return int(data.get("keys", {}).get(code, {}).get("uses", 0))

    async def get_all_keys(self) -> Dict[str, int]:
        """Trả về dict {code: số_lượt_còn} cho tất cả key."""
        async with self._lock:
            data = self._load()
            keys = data.get("keys", {})
            return {code: int(info.get("uses", 0)) for code, info in keys.items()}

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

    async def create_artifacts_page(self, title: str, artifacts: list, repo: str, run_id, owner: str) -> Optional[str]:
        """Tạo trang Telegraph với danh sách artifacts. Trả về URL."""
        await self._ensure_token()
        if not self._token:
            return None

        # Xây dựng nội dung trang
        content = [
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
                                    run_id=run_id, owner=config.GITHUB_OWNER
                                )

                        if telegraph_url:
                            buttons.append([InlineKeyboardButton("📦 Xem & Tải file", url=telegraph_url)])
                        buttons.append([InlineKeyboardButton("🌐 Xem trên GitHub", url=html_url)])
                        kb = InlineKeyboardMarkup(buttons)

                        chat_id = job["chat_id"]
                        user_id = job["user_id"]
                        user_name = job.get("user_name", "")
                        if not user_name:
                            user_name = str(user_id)
                        mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                        icon = "✅" if conclusion == "success" else "❌" if conclusion == "failure" else "⚠️"
                        
                        text = (
                            f"{icon} <b>Build {job.get('type','?').upper()} kết thúc!</b>\n"
                            f"📌 Trạng thái: <b>{conclusion.upper()}</b>\n"
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
        _, code, uses = update.message.text.strip().split(maxsplit=2)
        uses = int(uses)
    except Exception:
        return await update.message.reply_text("Cú pháp: /key {mã} {số_lượt}")
    await storage.set_key(code, uses)
    await update.message.reply_text(f"Đã set key `{code}` với {uses} lượt.", parse_mode=constants.ParseMode.MARKDOWN)


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
    for i, (code, uses) in enumerate(keys.items(), 1):
        status = f"✅ {uses} lượt" if uses > 0 else "❌ Hết lượt"
        lines.append(f"{i}. <code>{code}</code> — {status}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{update.message.message_id}")]])
    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.HTML, reply_markup=kb)

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = await update.message.reply_text("🏓 Pong! Bot đang hoạt động bình thường.")
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=update.message.chat_id, data=update.message.message_id)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    
    if not await is_admin(user.id, storage):
        return

    jobs = await storage.get_jobs()
    active_jobs = [j for j in jobs if j.get("status") in ("dispatched", "in_progress")]
    
    if not active_jobs:
        m = await update.message.reply_text("ℹ️ Hiện không có tiến trình build nào đang chạy.")
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        return

    for job in active_jobs:
        job_id = job.get("_id")
        created_at = datetime.fromisoformat(job.get("created_at"))
        elapsed = datetime.now(timezone.utc) - created_at
        
        # Estimate remaining time: assume 45 minutes total (2700 seconds)
        total_estimated_s = 2700
        remaining_s = max(0, total_estimated_s - elapsed.total_seconds())
        rem_m, rem_s = divmod(remaining_s, 60)
        
        mention = f'<a href="tg://user?id={job.get("user_id")}">{job.get("user_name", job.get("user_id"))}</a>'
        text = (
            f"🔄 <b>Job #{job_id} đang chạy</b>\n"
            f"👤 Yêu cầu bởi: {mention}\n"
            f"⏱️ Đã chạy: {int(elapsed.total_seconds() // 60)} phút\n"
            f"⏳ Ước tính còn: ~{int(rem_m)} phút\n"
            f"🔗 Tình trạng: <b>{job.get('status')}</b>"
        )
        
        buttons = [
            [InlineKeyboardButton("Đang lấy link...", callback_data="none")],
            [InlineKeyboardButton("Hủy Build ❌", callback_data=f"runctl:cancel:gki:{job.get('run_id', 0)}")]
        ]
        
        # If we have a run_id, give proper github links
        if job.get("run_id"):
            run_id = job["run_id"]
            repo = job["repo"]
            url = f"https://github.com/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
            buttons[0] = [InlineKeyboardButton("Xem trên GitHub 🌐", url=url)]
            
        buttons.append([InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{update.message.message_id}")])
        kb = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(text, parse_mode=constants.ParseMode.HTML, reply_markup=kb)

def _run_button_text(repo_label: str, run: dict) -> str:
    n = run.get("run_number")
    status = run.get("status")
    name = run.get("name") or run.get("display_title") or "workflow"
    return f"{repo_label} • #{n} • {status} • {name[:24]}"

async def show_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, message_to_edit=None, cmd_msg_id: int = 0):
    gh: GitHubAPI = context.application.bot_data["gh"]
    storage: StorageBase = context.application.bot_data["storage"]
    repo = config.GKI_REPO
    
    builds = await storage.get_successful_builds()
    if not builds:
        # Tự động lấy lịch sử cũ nếu chưa có
        url = f"{gh.base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs?status=success&per_page=10"
        out = await gh._request("GET", url)
        runs = out.get("json", {}).get("workflow_runs", []) if isinstance(out.get("json"), dict) else []
        for r in reversed(runs):
            # parse the time dummy
            try:
                dt_obj = datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                dt_obj = datetime.now(timezone.utc)
            # manually inject to cache
            build = {
                "run_id": r["id"],
                "user_id": 0, # Unknown old user
                "branch": r.get("head_branch", "unknown"),
                "timestamp": dt_obj.isoformat()
            }
            storage_data = storage._load()
            bl = storage_data.setdefault("successful_builds", [])
            bl.insert(0, build)
            storage_data["successful_builds"] = bl[:50]
            storage._save(storage_data)
        
        builds = await storage.get_successful_builds()

    if not builds:
        text = "Chưa có bản build thành công nào được lưu."
        if message_to_edit:
            return await message_to_edit.edit_text(text)
        else:
            return await update.message.reply_text(text)
            
    items_per_page = 5
    total_pages = max(1, (len(builds) + items_per_page - 1) // items_per_page)
    if page < 1: page = total_pages
    if page > total_pages: page = 1
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    current_builds = builds[start_idx:end_idx]
    
    if message_to_edit:
        await message_to_edit.edit_text("⏳ Đang tải thông tin trang...")
    else:
        message_to_edit = await update.message.reply_text("⏳ Đang tải thông tin trang...")
        
    text = f"🗂 **Danh sách các bản build GKI thành công (Trang {page}/{total_pages})**\n\n"
    
    # Render text for the 5 items
    for i, b in enumerate(current_builds):
        run_id = b["run_id"]
        user_id = b["user_id"]
        branch = b["branch"]
        
        # Format time
        try:
            dt = datetime.fromisoformat(b["timestamp"]).replace(tzinfo=timezone.utc)
            dt_local = dt + timedelta(hours=7)
            time_str = dt_local.strftime("%H:%M %d/%m/%Y")
        except:
            time_str = "Unknown"
            
        repo_url = f"https://github.com/{config.GITHUB_OWNER}/{repo}"
        html_url = f"{repo_url}/actions/runs/{run_id}"
        
        # Tạo trang Telegraph cho artifacts
        telegraph: TelegraphAPI = context.application.bot_data.get("telegraph")
        artifacts = await gh.list_artifacts_for_run(repo, run_id)
        telegraph_url = None
        if artifacts["status"] == 200:
            arr = artifacts["json"].get("artifacts", [])
            if arr and telegraph:
                telegraph_url = await telegraph.create_artifacts_page(
                    title=f"Build GKI #{run_id}",
                    artifacts=arr, repo=repo,
                    run_id=run_id, owner=config.GITHUB_OWNER
                )
        
        if telegraph_url:
            artifact_str = f"<a href='{telegraph_url}'>📦 Xem & Tải file</a>"
        else:
            artifact_str = "Không có file"
        
        if user_id == 0:
            mention = "Hệ thống cũ"
        else:
            user_name = b.get("user_name", "")
            if not user_name:
                user_name = str(user_id)
            mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
            
        text += f"**{start_idx + i + 1}. Build #{run_id}**\n"
        text += f"👤 Từ: {mention} | 🌿 Nhánh: <code>{branch}</code>\n"
        text += f"🕒 {time_str} | 🔗 <a href='{html_url}'>GitHub</a>\n"
        text += f"📦 Tải về: {artifact_str}\n\n"
        
    # Pagination buttons + Close
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

async def cb_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
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
    gh: GitHubAPI = context.application.bot_data["gh"]
    q = update.callback_query
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
    gh: GitHubAPI = context.application.bot_data["gh"]
    q = update.callback_query
    await q.answer()
    _, action, repo_tag, run_id_str = q.data.split(":", 3)
    run_id = int(run_id_str)
    repo = config.GKI_REPO
    if action == "cancel":
        res = await gh.cancel_run(repo, run_id)
        if res["status"] in (202, 204):
            # Đợi 1 chút rồi xóa luôn run dở
            import asyncio
            await asyncio.sleep(3)
            await gh.delete_run(repo, run_id)
            m = await q.edit_message_text(f"✅ Đã hủy và xóa run #{run_id}.")
        else:
            m = await q.edit_message_text(f"❌ Hủy thất bại: {res['status']}")
        if context.job_queue and m:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
    elif action == "close":
        # Xóa cả tin nhắn bot và lệnh gọi
        chat_id = q.message.chat_id
        try:
            await q.delete_message()
        except Exception:
            pass
        # run_id_str ở đây thực chất là cmd_msg_id (nếu có)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=run_id)
        except Exception:
            pass

async def check_user_job_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    storage: StorageBase = context.application.bot_data["storage"]
    if await is_admin(user.id, storage):
        return True
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
    await q.answer()
    chat_id = q.message.chat_id
    # Parse cmd_msg_id từ callback data
    cmd_msg_id = 0
    if ":" in q.data:
        try:
            cmd_msg_id = int(q.data.split(":")[1])
        except:
            pass
    # Xóa tin nhắn bot
    try:
        await q.delete_message()
    except Exception:
        pass
    # Xóa lệnh gọi
    if cmd_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=cmd_msg_id)
        except Exception:
            pass

def main():
    storage = JSONStorage(DATA_JSON)
    gh = GitHubAPI(config.GITHUB_TOKEN, config.GITHUB_OWNER)
    telegraph = TelegraphAPI(storage)

    request = HTTPXRequest(http_version="1.1")
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).request(request).build()
    app.bot_data["storage"] = storage
    app.bot_data["gh"] = gh
    app.bot_data["telegraph"] = telegraph

    # Owner-only commands
    app.add_handler(CommandHandler("key", cmd_key, filters=filters.User(user_id=config.OWNER_ID)))
    app.add_handler(CommandHandler("keys", cmd_keys, filters=filters.User(user_id=config.OWNER_ID)))

    # Admin commands (owner + static admins in .env)
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CallbackQueryHandler(cb_list_page, pattern=r"^listpage:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_close_msg, pattern=r"^closemsg"))
    app.add_handler(CallbackQueryHandler(cb_run_controls, pattern=r"^run:gki:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_run_control_action, pattern=r"^runctl:(cancel|close):gki:\d+$"))

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
    app.post_init = _post_init

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()