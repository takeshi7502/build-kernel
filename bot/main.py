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
from oki import build_oki_conversation
from buildsave import build_buildsave_conversation
from permissions import is_owner, is_admin
import aiohttp.web as aiohttp_web
from storage import HybridStorage
from web_sync import get_realtime_data

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING
)
logger = logging.getLogger("gww-bot")

import os
DATA_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json")


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


async def update_batch_message(batch_id: str, storage: HybridStorage, bot):
    try:
        jobs = await storage.get_jobs_by_batch(batch_id)
        if not jobs:
            return
            
        first_job = jobs[0]
        chat_id = first_job.get("chat_id")
        msg_id = first_job.get("batch_msg_id")
        variant = first_job.get("bs_variant", "")
        
        if not chat_id or not msg_id:
            return
            
        completed_count = sum(1 for j in jobs if j.get("status") == "completed")
        success_count = sum(1 for j in jobs if j.get("status") == "completed" and j.get("conclusion") == "success")
        total_count = len(jobs)
        
        # Danh sách version rút gọn
        vers = [j.get("bs_full_ver", "") for j in jobs]
        prefix = ""
        short_vers = []
        if vers:
            parts = vers[0].split(".")
            if len(parts) >= 2:
                prefix = f"{parts[0]}.{parts[1]}."
            for v in vers:
                if v.startswith(prefix):
                    short_vers.append(v[len(prefix):])
                else:
                    short_vers.append(v)
        
        if len(short_vers) > 3:
            v_str = f"{prefix}{','.join(short_vers[:3])}..."
        else:
            v_str = f"{prefix}{','.join(short_vers)}"

        # Header text
        if completed_count == total_count:
            if success_count == 0:
                lines = [
                    f"❌ <b>Đã huỷ/thất bại toàn bộ batch!</b>",
                    f"🔨 {variant} — {v_str}",
                    f"⏳ Tiến trình: {completed_count}/{total_count}"
                ]
            else:
                lines = [
                    f"✅ <b>Đã hoàn thành batch build!</b>",
                    f"🔨 {variant} — {v_str}",
                    f"⏳ Tiến trình: {completed_count}/{total_count} ({success_count} thành công)"
                ]
        else:
            lines = [
                f"✅ <b>Đang build kernel cho lưu trữ!</b>",
                f"🔨 {variant} — {v_str}",
                f"⏳ Tiến trình: {completed_count}/{total_count}"
            ]
            
        import config
        
        # Determine the current running github link
        current_run_id = None
        for j in jobs:
            if j.get("status") in ("dispatched", "in_progress", "running"):
                current_run_id = j.get("run_id")
                break

        if current_run_id:
            github_url = f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{current_run_id}"
        else:
            github_url = f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions"
            
        lines.append(f"<blockquote>Xem: <a href='{github_url}'>Github</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a> ❞</blockquote>")
        
        text = "\n".join(lines)
        
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"update_batch_message error: {e}")

async def poller(app):
    gh: GitHubAPI = app.bot_data["gh"]
    storage: HybridStorage = app.bot_data["storage"]

    async def cleanup_task():
        while True:
            try:
                deleted_msg = await storage.delete_old_messages(24)
                deleted_jobs = await storage.delete_old_jobs(30) # Lưu lịch sử Bot Build trong 30 ngày thay vì 7 ngày
                if deleted_msg or deleted_jobs:
                    logger.info("Cleanup: %d messages, %d jobs", deleted_msg, deleted_jobs)
            except Exception as e:
                logger.error("Cleanup error: %s", e)
            await asyncio.sleep(3600)

    app.create_task(cleanup_task())

    while True:
        try:
            # 1. Dispatch buildsave queue
            active_bs = await storage.get_active_buildsave_count()
            if active_bs == 0:
                next_bs = await storage.get_next_queued_buildsave()
                if next_bs:
                    inputs = next_bs.get("inputs", {})
                    res = await gh.dispatch_workflow(
                        repo=next_bs["repo"],
                        workflow_file=next_bs["workflow_file"],
                        ref=next_bs["ref"],
                        inputs=inputs
                    )
                    now_iso = datetime.now(timezone.utc).isoformat()
                    if res.get("status") in (201, 202, 204):
                        await storage.update_job(next_bs["_id"], {
                            "status": "dispatched",
                            "created_at": now_iso
                        })
                    else:
                        await storage.update_job(next_bs["_id"], {
                            "status": "completed",
                            "conclusion": "failure",
                            "notified": True,  # skip polling notification, silently fail
                            "created_at": now_iso
                        })
                    
                    batch_id = next_bs.get("batch_id")
                    if batch_id:
                        await update_batch_message(batch_id, storage, app.bot)
            
            # 2. Polling for unnotified jobs
            jobs = await storage.list_unnotified_jobs()
            for job in jobs:
                try:
                    if job.get("status") == "queued":
                        continue

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
                            # Không gửi tin nhắn hoàn thành riêng lẻ cho lệnh buildsave (đã có batch realtime info)
                            if job.get("type") != "buildsave":
                                msg = await app.bot.send_message(
                                    chat_id=chat_id, text=text,
                                    reply_markup=kb,
                                    parse_mode=constants.ParseMode.HTML,
                                    disable_web_page_preview=True
                                )
                                await storage.track_message(msg.message_id, chat_id, user_id)

                            # Cập nhật trạng thái job
                            await storage.update_job(job["_id"], {
                                "status": "completed",
                                "conclusion": conclusion,
                                "notified": True
                            })

                            if conclusion == "success":
                                await storage.add_successful_build(run_id, user_id, job.get("ref", "unknown"), user_name)
                                # Tự động gửi PM cấu hình cho GKI
                                try:
                                    if job.get("type", "gki") == "gki":
                                        await send_saved_config(app, run_id, job, user_id)
                                except Exception as e:
                                    logger.error("Auto PM save config failed: %s", e)

                                # Buildsave: cập nhật link tải xuống vào JSON và gửi thông báo đặc biệt
                                if job.get("type") == "buildsave":
                                    try:
                                        await _update_buildsave_download_link(job, run_id, app)
                                    except Exception as e:
                                        logger.error("buildsave JSON update failed: %s", e)

                            # Cập nhật message dạng batch nếu là buildsave
                            if job.get("type") == "buildsave" and job.get("batch_id"):
                                await update_batch_message(job["batch_id"], storage, app.bot)

                        except Exception as e:
                            logger.error("Send notification / Post completion failed: %s", e)
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


async def _send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    kwargs.pop('quote', None)
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id if (update.effective_message and update.effective_message.is_topic_message) else None
    return await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text, **kwargs)

async def _safe_delete_user_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Silently delete the user's message immediately."""
    if update.message:
        try:
            await update.message.delete()
        except:
            pass


async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
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
                await _send_msg(update, context, f"🗑️ Đã xoá key <code>{code}</code>.", parse_mode=constants.ParseMode.HTML)
            else:
                await _send_msg(update, context, f"⚠️ Key <code>{code}</code> không tồn tại.", parse_mode=constants.ParseMode.HTML)
            return
        else:
            uses = int(action)
    except Exception:
        return await _send_msg(update, context, "Cú pháp: /key {mã} {số_lượt|delete}")
        
    await storage.set_key(code, uses, vip=False)
    await _send_msg(update, context, f"✅ Đã set key <code>{code}</code> với {uses} lượt.", parse_mode=constants.ParseMode.HTML)


async def cmd_keyvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not is_owner(user.id):
        return
    try:
        _, code, uses = update.message.text.strip().split(maxsplit=2)
        uses = int(uses)
    except Exception:
        return await _send_msg(update, context, "Cú pháp: /keyvip {mã} {số_lượt}")
    await storage.set_key(code, uses, vip=True)
    await _send_msg(update, context,
        f"💎 Đã tạo VIP key <code>{code}</code> với {uses} lượt (không giới hạn 1h).",
        parse_mode=constants.ParseMode.HTML
    )


async def cmd_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not is_owner(user.id):
        return
    keys = await storage.get_all_keys()
    if not keys:
        m = await _send_msg(update, context, "ℹ️ Chưa có key nào được tạo.")
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
    await _send_msg(update, context, "\n".join(lines), parse_mode=constants.ParseMode.HTML, reply_markup=kb)

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await _send_msg(
            update,
            context,
            "🏓 <b>Pong!</b>\nBot đang hoạt động bình thường.",
            parse_mode=constants.ParseMode.HTML,
        )


async def dm_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all: tự động lưu chat_id của mọi user DM bot."""
    chat = update.effective_chat
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if chat and user:
        if chat.type == "private":
            await storage.track_dm_user(user.id, chat.id)
        elif chat.type in ("group", "supergroup"):
            title = chat.title or ""
            await storage.track_group(chat.id, title)


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin gửi thông báo đến tất cả user đã từng DM bot và các nhóm bot đã tham gia."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return

    # Lấy danh sách admin để loại trừ
    admin_ids = set()
    admin_ids.add(config.OWNER_ID)
    admin_ids.update(config.ADMIN_IDS)
    dynamic_admins = await storage.get_admin_ids()
    admin_ids.update(dynamic_admins)

    dm_users = await storage.get_dm_users()
    dm_targets = [u for u in dm_users if u.get("user_id") not in admin_ids]
    group_targets = await storage.get_group_chats()

    if not dm_targets and not group_targets:
        await _send_msg(update, context, "⚠️ Chưa có user hay nhóm nào trong danh sách để gửi.")
        return

    replied = update.message.reply_to_message if update.message else None
    text_body = " ".join(context.args) if context.args else ""

    if not replied and not text_body:
        await _send_msg(update, context,
            "📌 <b>Cách dùng:</b>\n"
            "• <code>/chat Nội dung thông báo</code>\n"
            "• Reply một tin nhắn + gõ <code>/chat</code>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    dm_ok = dm_fail = 0
    grp_ok = grp_fail = 0

    # Gửi tới tất cả user DM
    for u in dm_targets:
        cid = u.get("chat_id")
        try:
            if replied:
                await context.bot.forward_message(
                    chat_id=cid,
                    from_chat_id=replied.chat_id,
                    message_id=replied.message_id
                )
            else:
                await context.bot.send_message(chat_id=cid, text=text_body)
            dm_ok += 1
        except Exception:
            dm_fail += 1

    # Gửi tới tất cả nhóm
    for g in group_targets:
        cid = g.get("chat_id")
        try:
            if replied:
                await context.bot.forward_message(
                    chat_id=cid,
                    from_chat_id=replied.chat_id,
                    message_id=replied.message_id
                )
            else:
                await context.bot.send_message(chat_id=cid, text=text_body)
            grp_ok += 1
        except Exception:
            grp_fail += 1

    total_ok = dm_ok + grp_ok
    total = dm_ok + dm_fail + grp_ok + grp_fail
    await _send_msg(update, context,
        f"📢 Đã gửi <b>{total_ok}/{total}</b> điểm nhận.\n"
        f"• User DM: {dm_ok}/{dm_ok + dm_fail}\n"
        f"• Nhóm: {grp_ok}/{grp_ok + grp_fail}",
        parse_mode=constants.ParseMode.HTML
    )




async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hien danh sach lenh huong dan."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    admin = await is_admin(user.id, storage)

    text = (
        "📖 <b>Danh sách lệnh Bot</b>\n\n"
        "📌 <b>Ai cũng dùng được:</b>\n"
        "<blockquote>"
        "<b>/gki</b> <code>&lt;key&gt;</code> — Build GKI Kernel\n"
        "<b>/oki</b> <code>&lt;key&gt;</code> — Build OKI Kernel\n"
        "<b>/ping</b> — Kiểm tra bot hoạt động\n"
        "<b>/help</b> — Hiện hướng dẫn này\n"
        "</blockquote>"
    )

    if admin:
        text += (
            "\n🔒 <b>Admin:</b>\n"
            "<blockquote>"
            "<b>/key</b> <code>&lt;code&gt; &lt;uses&gt;</code> — Tạo/sửa key\n"
            "<b>/keyvip</b> <code>&lt;code&gt; &lt;uses&gt;</code> — Tạo VIP key\n"
            "<b>/keys</b> — Xem danh sách key\n"
            "<b>/st</b> — Xem build đang chạy\n"
            "<b>/list</b> — Lịch sử build thành công\n"
            "<b>/chat</b> <code>&lt;nội dung&gt;</code> — Broadcast cho all user\n"
            "</blockquote>"
        )

    text += (
        "\n🌐 <b>Dashboard:</b> "
        "<a href='https://kernel.takeshi.dev/'>kernel.takeshi.dev</a>"
    )

    await _send_msg(update, context, text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE, message_to_edit=None, cmd_msg_id=0):
    if not message_to_edit:
        await _safe_delete_user_msg(update, context)
        cmd_msg_id = update.message.message_id if update.message else 0
        
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    
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
                active_runs.append(r)

    if not active_runs:
        text = "ℹ️ Hiện không có tiến trình build nào đang chạy."
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Làm mới", callback_data=f"refresh_st:{cmd_msg_id}"), InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{cmd_msg_id}")]
        ])
        if message_to_edit:
            try:
                await message_to_edit.edit_text(text, reply_markup=kb)
            except Exception:
                pass
        else:
            m = await _send_msg(update, context, text, reply_markup=kb)
            if context.job_queue:
                context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
                if update.message:
                    context.job_queue.run_once(_del_msg_job, when=60, chat_id=update.message.chat_id, data=cmd_msg_id)
        return


    # Lấy danh sách jobs local để map user_id nếu có
    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}

    # Gom buildsave queued theo batch_id
    queued_bs = [j for j in jobs if j.get("type") == "buildsave" and j.get("status") == "queued"]
    batch_queued = {}
    for bj in queued_bs:
        bid = bj.get("batch_id")
        if bid:
            batch_queued.setdefault(bid, []).append(bj)

    lines = []
    seen_batches = set()

    for idx, run in enumerate(active_runs, 1):
        run_id = run["id"]
        status = run["status"]
        name = run.get("name") or run.get("display_title") or "workflow"
        job = run_to_job.get(run_id, {})

        created_at_str = run.get("created_at")
        elapsed_min = 0
        if created_at_str:
            try:
                created_at_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                elapsed = datetime.now(timezone.utc) - created_at_dt
                elapsed_min = int(elapsed.total_seconds() // 60)
            except:
                pass

        user_id = job.get("user_id", "Unknown")
        user_name = job.get("user_name", "Unknown")
        mention = f'<a href="tg://user?id={user_id}">{user_name}</a>' if user_id != "Unknown" else "GitHub / Manual"

        job_type = job.get("type", "gki")
        if job_type == "buildsave":
            type_label = "🗂 [Web Build]"
        elif job_type == "oki":
            type_label = "📱 [OKI Build]"
        else:
            type_label = "🤖 [Bot Build]"

        if idx > 1:
            lines.append("")

        lines.append(f"<b>{type_label}</b> — {mention} <code>#{run_id}</code>")
        lines.append(f"┠ <b>Trạng thái:</b> {status} ({name[:25]}) — ⏱ {elapsed_min} phút")

        batch_id = job.get("batch_id")
        if job_type == "buildsave" and batch_id and batch_id not in seen_batches:
            seen_batches.add(batch_id)
            q_jobs = sorted(batch_queued.get(batch_id, []), key=lambda x: x.get("batch_index", 0))
            current_ver = job.get("bs_full_ver", "đang build")
            lines.append(f"┠ 🔄 Đang build: <code>{current_ver}</code>")
            if q_jobs:
                wv = [j.get("bs_full_ver", "?") for j in q_jobs[:5]]
                lines.append(f"┠ ⏳ Hàng chờ: <code>{', '.join(wv)}</code>" + (f" (+{len(q_jobs)-5})" if len(q_jobs) > 5 else ""))
            lines.append(f"┠ Huỷ sub này → /cancel_{run_id}")
            lines.append(f"┖ Huỷ TOÀN BỘ batch → /cancelbatch_{batch_id.replace('-','')[:16]}")
        else:
            lines.append(f"┖ Huỷ job → /cancel_{run_id}")

    # Hiển thị các batch đang chờ chưa chạy
    for bid, bj_list in batch_queued.items():
        if bid not in seen_batches and bj_list:
            bj_list = sorted(bj_list, key=lambda x: x.get("batch_index", 0))
            vers = [j.get("bs_full_ver","?") for j in bj_list[:5]]
            lines.append("")
            lines.append(f"<b>🗂 [Web Build — Hàng chờ]</b>")
            lines.append(f"┠ ⏳ {', '.join(vers)}" + (f" (+{len(bj_list)-5})" if len(bj_list) > 5 else ""))
            lines.append(f"┖ Huỷ TOÀN BỘ → /cancelbatch_{bid.replace('-','')[:16]}")

    msg_text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Làm mới", callback_data=f"refresh_st:{cmd_msg_id}"),
            InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{cmd_msg_id}")
        ]
    ])
    if message_to_edit:
        try:
            await message_to_edit.edit_text(msg_text, parse_mode=constants.ParseMode.HTML, reply_markup=kb)
        except Exception:
            pass
    else:
        await _send_msg(update, context, msg_text, parse_mode=constants.ParseMode.HTML, reply_markup=kb)

async def cb_refresh_st(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Đang tải dữ liệu mới nhất...")
    try:
        parts = q.data.split(":")
        cmd_msg_id = int(parts[1]) if len(parts) > 1 else 0
    except:
        cmd_msg_id = 0
    await cmd_status(update, context, message_to_edit=q.message, cmd_msg_id=cmd_msg_id)

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
        if run.get("status") != "completed":
            return False
        if run.get("conclusion") != "success":
            return False
        return True

    github_runs = [r for r in github_runs if _is_target_success_run(r)]

    if not github_runs:
        text = "ℹ️ Hiện không có lịch sử build GKI nào thành công."
        if message_to_edit:
            return await message_to_edit.edit_text(text)
        else:
            return await _send_msg(update, context, text)

    items_per_page = 5
    total_pages = max(1, (len(github_runs) + items_per_page - 1) // items_per_page)
    if page < 1:
        page = total_pages
    if page > total_pages:
        page = 1

    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    current_builds = github_runs[start_idx:end_idx]

    storage: HybridStorage = context.application.bot_data["storage"]
    jobs = await storage.get_jobs()
    run_to_job = {j.get("run_id"): j for j in jobs if j.get("run_id")}

    if message_to_edit:
        await message_to_edit.edit_text("⏳ Đang tải thông tin trang...")
    else:
        message_to_edit = await _send_msg(update, context, "⏳ Đang tải thông tin trang...")

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
            user_id = job.get("user_id", 0)
            user_name = job.get("user_name", "")
            if not user_name:
                user_name = str(user_id)
            if user_id == 0:
                mention = "Hệ thống cũ"
            else:
                mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
        else:
            actor = r.get("actor", {}).get("login", "Unknown")
            mention = f"GitHub / {actor}"

        text += f"<b>{start_idx + i + 1}. Run #{run_id}</b> by {mention}\n"
        text += f"Time: {time_str}\n"
        text += f"Xoá: /delete_{run_id}\n"
        text += f"<blockquote><b>Xem : <a href='{html_url}'>Github</a> | <a href='{nightly_url}'>File</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>\n\n"

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
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
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
    storage: HybridStorage = context.application.bot_data["storage"]
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
    storage: HybridStorage = context.application.bot_data["storage"]
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
    storage: HybridStorage = context.application.bot_data["storage"]
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
    storage: HybridStorage = context.application.bot_data["storage"]
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
            m = await update.message.reply_text(f"⚠️ Chỉ được 1 job/1h. Vui lòng đợi {remaining} phút.", quote=False)
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
            return False
    return True

async def cb_close_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    storage: HybridStorage = context.application.bot_data["storage"]
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
    storage: HybridStorage = context.application.bot_data["storage"]
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

async def cmd_cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Huỷ toàn bộ batch buildsave theo batch_id."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    gh: GitHubAPI = context.application.bot_data["gh"]

    if not await is_admin(user.id, storage):
        return

    text = update.message.text or ""
    # Format: /cancelbatch_<uuid>
    import re as _re
    m = _re.match(r"^/cancelbatch_([\w-]+)", text.strip())
    if not m:
        await update.message.reply_text("❌ Sai cú pháp.")
        return

    batch_prefix = m.group(1).replace("-", "")  # Đã bỏ gạch ngang khi hiển thị
    jobs = await storage.get_jobs()
    # So sánh prefix 16 ký tự (đã bỏ gạch ngang UUID)
    batch_jobs = [j for j in jobs if j.get("batch_id", "").replace("-", "")[:16] == batch_prefix[:16]]

    if not batch_jobs:
        await update.message.reply_text(f"❌ Không tìm thấy batch `{batch_prefix[:8]}...`", parse_mode="Markdown")
        return

    cancelled = 0
    for j in batch_jobs:
        status = j.get("status", "")
        run_id = j.get("run_id")
        if status == "queued":
            # Huỷ job queued trong DB ngay
            await storage.update_job(j["_id"], {"status": "completed", "conclusion": "cancelled", "notified": True})
            cancelled += 1
        elif status in ("dispatched", "in_progress", "running") and run_id:
            # Gửi cancel lên GitHub
            res = await gh.cancel_run(j.get("repo", config.GKI_REPO), int(run_id))
            await storage.update_job(j["_id"], {"status": "completed", "conclusion": "cancelled", "notified": True})
            cancelled += 1

    msg = await update.message.reply_text(f"✅ Đã huỷ <b>{cancelled}/{len(batch_jobs)}</b> jobs trong batch.", parse_mode="HTML")
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=15, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=15, chat_id=update.message.chat_id, data=update.message.message_id)

async def cmd_cancel_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return
    
    text = update.message.text.strip()
    try:
        raw_cmd = text.split()[0]
        run_id_str = raw_cmd.split("_")[1].split("@")[0]
        run_id = int(run_id_str)
    except:
        return await _send_msg(update, context, "❌ ID không hợp lệ.")
        
    gh: GitHubAPI = context.application.bot_data["gh"]
    msg = await _send_msg(update, context, f"⏳ Đang gửi lệnh hủy Run #{run_id} lên GitHub...")
    res = await gh.cancel_run(config.GKI_REPO, run_id)
    
    if res["status"] in (202, 204):
        job = await storage.get_job_by_run_id(run_id)
        if job:
            await storage.update_job(job["_id"], {"status": "completed", "conclusion": "cancelled", "notified": True})
            if job.get("batch_id"):
                await update_batch_message(job["batch_id"], storage, context.application.bot)
        
        # Dọn dẹp github run actions list
        async def _cleanup_gh():
            for _ in range(10):
                await asyncio.sleep(5)
                rn = await gh.get_run(config.GKI_REPO, run_id)
                if rn["status"] == 200 and rn.get("json", {}).get("status") == "completed":
                    break
            await gh.delete_run(config.GKI_REPO, run_id)
        
        context.application.create_task(_cleanup_gh())
        await msg.edit_text(f"✅ Đã hủy thành công Run #{run_id}.")
    else:
        await msg.edit_text(f"❌ Lỗi hủy: {res['status']} {res.get('json', '')}")
        
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=10, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=10, chat_id=update.message.chat_id, data=update.message.message_id)

async def cmd_delete_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return
    
    text = update.message.text.strip()
    try:
        raw_cmd = text.split()[0]
        run_id_str = raw_cmd.split("_")[1].split("@")[0]
        run_id = int(run_id_str)
    except:
        return await _send_msg(update, context, "❌ ID không hợp lệ.")
        
    gh: GitHubAPI = context.application.bot_data["gh"]
    
    msg = await _send_msg(update, context, f"⏳ Đang gửi lệnh xoá Run #{run_id} lên GitHub...")
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

async def _update_buildsave_download_link(job: dict, run_id, app):
    """Cập nhật link tải xuống vào file JSON của web khi build lưu trữ hoàn tất."""
    import json as _json
    variant   = job.get("bs_variant", "")
    android   = job.get("bs_android", "")       # e.g. "android12"
    kernel_v  = job.get("bs_kernel_ver", "")    # e.g. "5.10"
    sub_level = job.get("bs_sub_level", "")     # e.g. "149"
    full_ver  = job.get("bs_full_ver", "")      # e.g. "5.10.149"

    if not all([variant, android, kernel_v, sub_level]):
        logger.warning("buildsave: thiếu metadata để cập nhật JSON")
        return

    nightly_link = (
        f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}"
        f"/actions/runs/{run_id}"
    )

    # Đường dẫn file JSON tương đối từ bot/ → web/data/<android>/<kernel>.json
    bot_dir  = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(bot_dir, "..", "web", "data", android, f"{kernel_v}.json")
    json_path = os.path.normpath(json_path)

    if not os.path.exists(json_path):
        logger.error("buildsave: không tìm thấy file JSON: %s", json_path)
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = _json.load(f)

    updated = False
    for entry in data.get("entries", []):
        if entry.get("kernel") == full_ver:
            if "downloads" not in entry:
                entry["downloads"] = {}
            entry["downloads"][variant] = nightly_link
            updated = True
            # Không break — có thể có nhiều entry cùng kernel ver (khác date)

    if not updated:
        logger.warning("buildsave: không tìm thấy entry kernel=%s trong %s", full_ver, json_path)
        return

    with open(json_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
    logger.warning("buildsave: đã cập nhật %s → %s = %s", json_path, variant, nightly_link)

    # Không gửi thông báo riêng lẻ từng sub
    # Thông báo tổng hợp sẽ do update_batch_message xử lý khi tất cả xong
    logger.info("buildsave: da cap nhat JSON cho %s %s", variant, full_ver)


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
    
    # Phục vụ static files cho source web mới (do webpack build ra)
    app.router.add_static('/dist', os.path.join(base_dir, 'web', 'dist'))
    app.router.add_static('/data', os.path.join(base_dir, 'web', 'data'))
    
    async def index(request):
        return aiohttp_web.FileResponse(os.path.join(base_dir, 'web', 'index.html'))
        
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

    storage = HybridStorage(
        DATA_JSON,
        config.MONGODB_URI,
        sync_mode=config.MONGODB_SYNC_MODE,
        writer_hostname=config.MONGODB_SYNC_WRITER_HOSTNAME,
    )
    gh = GitHubAPI(config.GITHUB_TOKEN, config.GITHUB_OWNER)
    telegraph = TelegraphAPI(storage)

    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()
    
    app.bot_data["storage"] = storage
    app.bot_data["gh"] = gh
    app.bot_data["telegraph"] = telegraph

    # Background tasks sẽ được chạy trong _post_init

    # Owner-only commands
    app.add_handler(CommandHandler("key", cmd_key, filters=filters.User(user_id=config.OWNER_ID)))
    app.add_handler(CommandHandler("keyvip", cmd_keyvip, filters=filters.User(user_id=config.OWNER_ID)))
    app.add_handler(CommandHandler("keys", cmd_keys, filters=filters.User(user_id=config.OWNER_ID)))

    # Admin commands (owner + static admins in .env)
    app.add_handler(CommandHandler("st", cmd_status))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("chat", cmd_broadcast))
    app.add_handler(CallbackQueryHandler(cb_list_page, pattern=r"^listpage:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_close_msg, pattern=r"^closemsg"))
    app.add_handler(CallbackQueryHandler(cb_refresh_st, pattern=r"^refresh_st"))
    app.add_handler(CallbackQueryHandler(cb_run_controls, pattern=r"^run:gki:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_run_control_action, pattern=r"^runctl:(cancel|close):gki:\d+(?::\d+)?$"))
    app.add_handler(CallbackQueryHandler(cb_save_run, pattern=r"^saverun:\d+$"))
    app.add_handler(MessageHandler(filters.Regex(r"^/cancel_\d+(?:@[\w_]+)?$"), cmd_cancel_run))
    app.add_handler(MessageHandler(filters.Regex(r"^/cancelbatch_[\w-]+(?:@[\w_]+)?$"), cmd_cancel_batch))
    app.add_handler(MessageHandler(filters.Regex(r"^/delete_\d+(?:@[\w_]+)?$"), cmd_delete_run))

    # DM user tracker (group=99, catch-all, lowest priority)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL, dm_tracker), group=99)

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

    # OKI conversation
    app.add_handler(build_oki_conversation(gh, storage, config))

    # /build — Build kernel lưu trữ (admin only)
    app.add_handler(build_buildsave_conversation(gh, storage, config))

    async def _post_init(app_):
        # Seed dm_users từ lịch sử jobs cũ
        seeded = await app_.bot_data["storage"].seed_dm_users_from_jobs()
        if seeded:
            logger.warning("Seeded %d DM users from job history", seeded)
        # Seed group_chats từ lịch sử jobs cũ (chat_id < 0)
        seeded_grp = await app_.bot_data["storage"].seed_groups_from_jobs()
        if seeded_grp:
            logger.warning("Seeded %d group chats from job history", seeded_grp)
        app_.create_task(app_.bot_data["storage"]._sync_with_cloud())
        app_.create_task(poller(app_))
        app_.create_task(start_web_server(app_))
    app.post_init = _post_init

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
