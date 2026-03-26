import os
# XÃ³a proxy Ä‘á»ƒ trÃ¡nh lá»—i káº¿t ná»‘i trÃªn mÃ´i trÆ°á»ng Termux/VPS cÃ³ proxy há»‡ thá»‘ng
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
    """Táº¡o trang Telegraph chá»©a link táº£i artifacts."""
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
        # Táº¡o tÃ i khoáº£n má»›i
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
        """Táº¡o trang Telegraph vá»›i danh sÃ¡ch artifacts. Tráº£ vá» URL."""
        await self._ensure_token()
        if not self._token:
            return None

        # XÃ¢y dá»±ng ná»™i dung trang
        content = [
            {"tag": "h4", "children": ["Cau hinh build"]},
            {"tag": "pre", "children": [self._format_build_config(config_inputs or {})]},
            {"tag": "hr"},
            {"tag": "h4", "children": [f"ðŸ“¦ Danh sÃ¡ch file táº£i vá»"]},
            {"tag": "p", "children": [f"Build: {title}"]},
            {"tag": "hr"},
        ]
        for a in artifacts:
            name = a["name"]
            dl_url = f"https://nightly.link/{owner}/{repo}/actions/runs/{run_id}/{name}.zip"
            content.append({"tag": "p", "children": [
                {"tag": "a", "attrs": {"href": dl_url}, "children": [f"ðŸ“¥ {name}.zip"]}
            ]})
        
        content.append({"tag": "hr"})
        gh_url = f"https://github.com/{owner}/{repo}/actions/runs/{run_id}"
        content.append({"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": gh_url}, "children": ["ðŸ”— Xem trÃªn GitHub"]}
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
    storage: HybridStorage = app.bot_data["storage"]

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
                        # Táº¡o trang Telegraph cho artifacts
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
                                InlineKeyboardButton("ðŸŒ Xem GitHub", url=html_url),
                                InlineKeyboardButton("ðŸ“¦ Táº£i file", url=telegraph_url)
                            ])
                        else:
                            buttons.append([InlineKeyboardButton("ðŸŒ Xem trÃªn GitHub", url=html_url)])
                        
                        buttons.append([InlineKeyboardButton("ðŸ“Š Web Dashboard", url="https://kernel.takeshi.dev/")])
                        kb = InlineKeyboardMarkup(buttons)

                        chat_id = job["chat_id"]
                        user_id = job["user_id"]
                        user_name = job.get("user_name", "")
                        if not user_name:
                            user_name = str(user_id)
                        mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                        icon = "âœ…" if conclusion == "success" else "âŒ" if conclusion == "failure" else "âš ï¸"
                        
                        created_at_dt = datetime.fromisoformat(job.get("created_at", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
                        elapsed = int((datetime.now(timezone.utc) - created_at_dt).total_seconds() // 60)

                        text = (
                            f"{icon} <b>Build {job.get('type','?').upper()} káº¿t thÃºc!</b>\n"
                            f"ðŸ“Œ Tráº¡ng thÃ¡i: <b>{conclusion.upper()}</b>\n"
                            f"â±ï¸ Thá»i gian: <b>{elapsed} phÃºt</b>\n"
                            f"ðŸ‘¤ NgÆ°á»i gá»­i: {mention}"
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
                                # Tá»± Ä‘á»™ng gá»­i tin nháº¯n lÆ°u cáº¥u hÃ¬nh
                                try:
                                    if job.get("type", "gki") == "gki":
                                        await send_saved_config(app, run_id, job, user_id)
                                except Exception as e:
                                    logger.error("Auto PM save config failed: %s", e)
                        except Exception as e:
                            logger.error("Send notification failed: %s", e)
                            await storage.update_job(job["_id"], {"notified": True})

                        # BÃ¡o cho nhá»¯ng ngÆ°á»i Ä‘ang Ä‘á»£i
                        waiters = await storage.get_waiters()
                        if waiters:
                            for w in waiters:
                                w_user_id = w["user_id"]
                                w_chat_id = w["chat_id"]
                                w_name = w.get("user_name", str(w_user_id))
                                w_mention = f'<a href="tg://user?id={w_user_id}">{w_name}</a>'
                                msg_waiter = f"ðŸ”” {w_mention} Æ¡i, tiáº¿n trÃ¬nh Ä‘Ã£ hoÃ n táº¥t! Báº¡n cÃ³ thá»ƒ dÃ¹ng lá»‡nh /gki láº¡i ngay bÃ¢y giá» nhÃ©."
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
                await _send_msg(update, context, f"ðŸ—‘ï¸ ÄÃ£ xoÃ¡ key <code>{code}</code>.", parse_mode=constants.ParseMode.HTML)
            else:
                await _send_msg(update, context, f"âš ï¸ Key <code>{code}</code> khÃ´ng tá»“n táº¡i.", parse_mode=constants.ParseMode.HTML)
            return
        else:
            uses = int(action)
    except Exception:
        return await _send_msg(update, context, "CÃº phÃ¡p: /key {mÃ£} {sá»‘_lÆ°á»£t|delete}")
        
    await storage.set_key(code, uses, vip=False)
    await _send_msg(update, context, f"âœ… ÄÃ£ set key <code>{code}</code> vá»›i {uses} lÆ°á»£t.", parse_mode=constants.ParseMode.HTML)


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
        return await _send_msg(update, context, "CÃº phÃ¡p: /keyvip {mÃ£} {sá»‘_lÆ°á»£t}")
    await storage.set_key(code, uses, vip=True)
    await _send_msg(update, context,
        f"ðŸ’Ž ÄÃ£ táº¡o VIP key <code>{code}</code> vá»›i {uses} lÆ°á»£t (khÃ´ng giá»›i háº¡n 1h).",
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
        m = await _send_msg(update, context, "â„¹ï¸ ChÆ°a cÃ³ key nÃ o Ä‘Æ°á»£c táº¡o.")
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        return
    lines = ["ðŸ”‘ <b>Danh sÃ¡ch Key</b>\n"]
    for i, (code, info) in enumerate(keys.items(), 1):
        uses = info["uses"]
        vip = info.get("vip", False)
        
        status = f"cÃ²n {uses} lÆ°á»£t" if uses > 0 else "Háº¿t lÆ°á»£t"
        if vip:
            icon = "ðŸ’Ž"
        elif uses > 0:
            icon = "âœ…"
        else:
            icon = "âŒ"
            
        lines.append(f"{i}. {icon}- <code>{code}</code> - {status}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("âŒ ÄÃ³ng", callback_data=f"closemsg:{update.message.message_id}")]])
    await _send_msg(update, context, "\n".join(lines), parse_mode=constants.ParseMode.HTML, reply_markup=kb)

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    m = await _send_msg(update, context, "ðŸ“ Pong! Bot Ä‘ang hoáº¡t Ä‘á»™ng bÃ¬nh thÆ°á»ng.")
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=update.message.chat_id, data=update.message.message_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    storage: HybridStorage = context.application.bot_data["storage"]
    user = update.effective_user
    chat = update.effective_chat
    # Track DM user
    if chat and chat.type == "private" and user:
        await storage.track_dm_user(user.id, chat.id)
    msg = (
        "ðŸ‘‹ Xin chÃ o! MÃ¬nh lÃ  Bot Build Kernel GKI.\n\n"
        "ðŸ¤– MÃ¬nh giÃºp tá»± Ä‘á»™ng hÃ³a quÃ¡ trÃ¬nh cáº¥u hÃ¬nh vÃ  biÃªn dá»‹ch (build) Kernel Android (GKI) qua GitHub Actions.\n\n"
        "ðŸ“Œ <b>CÃ¡c lá»‡nh cÆ¡ báº£n:</b>\n"
        "â€¢ /gki - Báº¯t Ä‘áº§u quÃ¡ trÃ¬nh chá»n vÃ  build Kernel\n"
        "â€¢ /ping - Kiá»ƒm tra tÃ¬nh tráº¡ng hoáº¡t Ä‘á»™ng cá»§a Bot\n\n"
        "<i>Ghi chÃº: Báº¡n cáº§n cÃ³ cáº¥u hÃ¬nh há»£p lá»‡ hoáº·c Ä‘Æ°á»£c Admin cáº¥p quyá»n Ä‘á»ƒ sá»­ dá»¥ng tÃ­nh nÄƒng build.</i>"
    )
    if update.message:
        await _send_msg(update, context, msg, parse_mode=constants.ParseMode.HTML)


async def dm_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all: tá»± Ä‘á»™ng lÆ°u chat_id cá»§a má»i user DM bot."""
    chat = update.effective_chat
    user = update.effective_user
    if chat and chat.type == "private" and user:
        storage: HybridStorage = context.application.bot_data["storage"]
        await storage.track_dm_user(user.id, chat.id)


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin gá»­i thÃ´ng bÃ¡o Ä‘áº¿n táº¥t cáº£ user Ä‘Ã£ tá»«ng DM bot."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return

    # Láº¥y danh sÃ¡ch admin Ä‘á»ƒ loáº¡i trá»«
    admin_ids = set()
    admin_ids.add(config.OWNER_ID)
    admin_ids.update(config.ADMIN_IDS)
    dynamic_admins = await storage.get_admin_ids()
    admin_ids.update(dynamic_admins)

    dm_users = await storage.get_dm_users()
    targets = [u for u in dm_users if u.get("user_id") not in admin_ids]

    if not targets:
        await _send_msg(update, context, "âš ï¸ ChÆ°a cÃ³ user nÃ o trong danh sÃ¡ch Ä‘á»ƒ gá»­i.")
        return

    replied = update.message.reply_to_message if update.message else None
    text_body = " ".join(context.args) if context.args else ""

    if not replied and not text_body:
        await _send_msg(update, context,
            "ðŸ“Œ <b>CÃ¡ch dÃ¹ng:</b>\n"
            "â€¢ <code>/chat Ná»™i dung thÃ´ng bÃ¡o</code>\n"
            "â€¢ Reply má»™t tin nháº¯n + gÃµ <code>/chat</code>",
            parse_mode=constants.ParseMode.HTML
        )
        return

    success = 0
    fail = 0
    for u in targets:
        cid = u.get("chat_id")
        try:
            if replied:
                # Forward tin nháº¯n gá»‘c
                await context.bot.forward_message(
                    chat_id=cid,
                    from_chat_id=replied.chat_id,
                    message_id=replied.message_id
                )
            else:
                await context.bot.send_message(chat_id=cid, text=text_body)
            success += 1
        except Exception:
            fail += 1

    await _send_msg(update, context,
        f"ðŸ“¢ ÄÃ£ gá»­i thÃ nh cÃ´ng <b>{success}/{success + fail}</b> user.",
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
        "/start — Bắt đầu sử dụng bot\n"
        "/gki — Build GKI Kernel (cần key nếu không phải admin)\n"
        "/oki — Build OKI Kernel\n"
        "/ping — Kiểm tra bot hoạt động\n"
        "/st — Xem build đang chạy\n"
        "/list — Lịch sử build thành công\n"
        "/help — Hiện hướng dẫn này\n"
    )

    if admin:
        text += (
            "\n🔒 <b>Chỉ Admin:</b>\n"
            "/key <code>&lt;code&gt; &lt;uses&gt;</code> — Tạo/sửa key\n"
            "/keyvip <code>&lt;code&gt; &lt;uses&gt;</code> — Tạo VIP key\n"
            "/keys — Xem danh sách key\n"
            "/chat <code>&lt;nội dung&gt;</code> — Broadcast cho all user\n"
            "/cancel_<code>&lt;run_id&gt;</code> — Hủy build\n"
            "/delete_<code>&lt;run_id&gt;</code> — Xóa run\n"
        )

    text += (
        "\n🌐 <b>Dashboard:</b> "
        "<a href='https://kernel.takeshi.dev/'>kernel.takeshi.dev</a>"
    )

    await _send_msg(update, context, text,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    
    if not await is_admin(user.id, storage):
        return

    gh: GitHubAPI = context.application.bot_data["gh"]
    
    # Láº¥y thÃ´ng tin run trá»±c tiáº¿p tá»« GitHub Ä‘á»ƒ luÃ´n chÃ­nh xÃ¡c nháº¥t
    active_runs = []
    for status in ["in_progress", "queued"]:
        url = f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs?status={status}&per_page=10"
        res = await gh._request("GET", url)
        if res.get("status") == 200:
            runs = res["json"].get("workflow_runs", [])
            for r in runs:
                active_runs.append(r)

    if not active_runs:
        m = await _send_msg(update, context, "â„¹ï¸ Hiá»‡n khÃ´ng cÃ³ tiáº¿n trÃ¬nh build nÃ o Ä‘ang cháº¡y.")
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
            if update.message:
                context.job_queue.run_once(_del_msg_job, when=60, chat_id=update.message.chat_id, data=update.message.message_id)
        return

    # Láº¥y danh sÃ¡ch jobs local Ä‘á»ƒ map user_id náº¿u cÃ³
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
            lines.append("") # thÃªm dÃ²ng trá»‘ng giá»¯a cÃ¡c job
            
        lines.append(f"<b>{idx}. Task by {mention} ( #{run_id}) Ä‘ang cháº¡y</b>")
        lines.append(f"â”  <b>ÄÃ£ cháº¡y</b> {elapsed_min}p - <b>Æ¯á»›c tÃ­nh cÃ²n</b> {rem_m}p")
        lines.append(f"â”  <b>TÃ¬nh tráº¡ng:</b> {status} ({name[:20]})")
        lines.append(f"â”– <b>Huá»· job</b> â†’ /cancel_{run_id}")

    msg_text = "\n".join(lines)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("âŒ ÄÃ³ng", callback_data=f"closemsg:{update.message.message_id}")]])
    await _send_msg(update, context, msg_text, parse_mode=constants.ParseMode.HTML, reply_markup=kb)

def _run_button_text(repo_label: str, run: dict) -> str:
    n = run.get("run_number")
    status = run.get("status")
    name = run.get("name") or run.get("display_title") or "workflow"
    return f"{repo_label} â€¢ #{n} â€¢ {status} â€¢ {name[:24]}"

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
        text = "â„¹ï¸ Hiá»‡n khÃ´ng cÃ³ lá»‹ch sá»­ build GKI nÃ o thÃ nh cÃ´ng."
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
        await message_to_edit.edit_text("â³ Äang táº£i thÃ´ng tin trang...")
    else:
        message_to_edit = await _send_msg(update, context, "â³ Äang táº£i thÃ´ng tin trang...")

    text = f"ðŸ—‚ <b>Danh sÃ¡ch cÃ¡c báº£n build GKI:</b>\n\n"

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

        # DÃ¹ng nightly.link theo run_id Ä‘á»ƒ má»Ÿ trang táº£i artifacts trá»±c tiáº¿p.
        # CÃ¡ch nÃ y trÃ¡nh gá»i artifacts API cho tá»«ng item nÃªn list load nhanh hÆ¡n.
        nightly_url = f"https://nightly.link/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
        artifact_str = f"ðŸ“¦ <a href='{nightly_url}'>Táº£i vá»</a>"

        job = run_to_job.get(run_id)
        if job:
            user_id = job.get("user_id", 0)
            user_name = job.get("user_name", "")
            if not user_name:
                user_name = str(user_id)
            if user_id == 0:
                mention = "Há»‡ thá»‘ng cÅ©"
            else:
                mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
        else:
            actor = r.get("actor", {}).get("login", "Unknown")
            mention = f"GitHub / {actor}"

        text += f"<b>{start_idx + i + 1}. Run #{run_id}</b> by {mention}\n"
        text += f"Time: {time_str}\n"
        text += f"XoÃ¡: /delete_{run_id}\n"
        text += f"<blockquote><b>Xem : <a href='{html_url}'>Github</a> | <a href='{nightly_url}'>File</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>\n\n"

    kb = []
    if total_pages > 1:
        kb.append([
            InlineKeyboardButton("â¬…ï¸ TrÆ°á»›c", callback_data=f"listpage:{page-1}"),
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="none"),
            InlineKeyboardButton("Sau âž¡ï¸", callback_data=f"listpage:{page+1}")
        ])
    kb.append([InlineKeyboardButton("âŒ ÄÃ³ng", callback_data=f"closemsg:{cmd_msg_id}")])
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
                lines.append(f"ðŸŒ¿ Build: {prefix}|{prefix_tk}.{s.strip()}")
                
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
    # TÃ¬m cmd_msg_id tá»« nÃºt ÄÃ³ng trong reply_markup hiá»‡n táº¡i
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
        [InlineKeyboardButton("Há»§y bá»", callback_data=f"runctl:cancel:{repo_tag}:{run_id}"),
         InlineKeyboardButton("ÄÃ³ng", callback_data=f"runctl:close:{repo_tag}:{run_id}")],
        [InlineKeyboardButton("Xem", url=view_url)]
    ]
    await q.edit_message_text(
        text=f"Run #{run_id} â€” GKI",
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
        # 1. Hiá»ƒn thá»‹ tráº¡ng thÃ¡i Ä‘ang há»§y
        await q.edit_message_text(f"â³ Äang gá»­i lá»‡nh há»§y run #{run_id}...")
        
        # 2. Gá»­i lá»‡nh há»§y
        res = await gh.cancel_run(repo, run_id)
        if res["status"] not in (202, 204):
            await q.edit_message_text(f"âŒ Gá»­i lá»‡nh há»§y tháº¥t báº¡i: HTTP {res['status']}")
            return
        
        # 3. Cáº­p nháº­t tráº¡ng thÃ¡i Ä‘ang chá»
        await q.edit_message_text(f"â³ ÄÃ£ gá»­i lá»‡nh há»§y run #{run_id}.\nÄang chá» xÃ¡c nháº­n tá»« GitHub...")
        
        # 4. Poll cho Ä‘áº¿n khi run thá»±c sá»± cancelled (tá»‘i Ä‘a 60s)
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
                        # Tá»± Ä‘á»™ng xoÃ¡ job sau khi cancel
                        await gh.delete_run(repo, run_id)
                        await storage.delete_job_by_run_id(run_id)
                        await q.edit_message_text(
                            f"âœ… <b>ÄÃ£ há»§y vÃ  xoÃ¡ thÃ nh cÃ´ng!</b>\n\n"
                            f"Run #{run_id} Ä‘Ã£ Ä‘Æ°á»£c há»§y vÃ  dá»n dáº¹p.",
                            parse_mode="HTML"
                        )
                    else:
                        await q.edit_message_text(
                            f"â„¹ï¸ Run #{run_id} Ä‘Ã£ hoÃ n táº¥t vá»›i káº¿t quáº£: <b>{conclusion}</b>",
                            parse_mode="HTML"
                        )
                    # XÃ³a tin nháº¯n lá»‡nh gá»‘c
                    if cmd_msg_id:
                        try:
                            await context.bot.delete_message(chat_id=q.message.chat_id, message_id=cmd_msg_id)
                        except Exception:
                            pass
                    # Tá»± xÃ³a sau 60s
                    if context.job_queue:
                        context.job_queue.run_once(_del_msg_job, when=60, chat_id=q.message.chat_id, data=q.message.message_id)
                    return
        
        # Timeout - váº«n chÆ°a cancelled sau 60s
        await q.edit_message_text(
            f"âš ï¸ ÄÃ£ gá»­i lá»‡nh há»§y run #{run_id} nhÆ°ng chÆ°a xÃ¡c nháº­n Ä‘Æ°á»£c.\n"
            f"Vui lÃ²ng kiá»ƒm tra trÃªn GitHub.",
            parse_mode="HTML"
        )
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, when=60, chat_id=q.message.chat_id, data=q.message.message_id)
            
    elif action == "close":
        # XÃ³a cáº£ tin nháº¯n bot vÃ  lá»‡nh gá»i
        chat_id = q.message.chat_id
        try:
            await q.delete_message()
        except Exception:
            pass
        # cmd_msg_id cÃ³ thá»ƒ Ä‘c truyá»n tá»« callback
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
            m = await update.message.reply_text(f"âš ï¸ Chá»‰ Ä‘Æ°á»£c 1 job/1h. Vui lÃ²ng Ä‘á»£i {remaining} phÃºt.", quote=False)
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
        
    lines = [f"ðŸ’¾ <b>LÆ¯U TRá»® Cáº¤U HÃŒNH GKI BUILD #{run_id}</b>"]
    # Get build date from job if available, else current time
    job_created_at = job.get("created_at")
    if job_created_at:
        try:
            dt = datetime.fromisoformat(job_created_at).replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            lines.append(f"ðŸ•’ <b>NgÃ y build</b>: <code>{dt.strftime('%H:%M %d/%m/%Y')}</code>\n")
        except:
            pass
    else:
        lines.append(f"ðŸ•’ <b>NgÃ y build</b>: <code>ChÆ°a rÃµ</code>\n")

    lines.append(f"â€¢ <b>KernelSU Variant</b>: <code>{inputs.get('kernelsu_variant', 'None')}</code>")
    lines.append(f"â€¢ <b>KernelSU Branch</b>: <code>{inputs.get('kernelsu_branch', 'None')}</code>")
    
    if inputs.get('version'):
        lines.append(f"â€¢ <b>Version Custom</b>: <code>{inputs.get('version')}</code>")

    lines.append(f"â€¢ <b>Compile BBG</b>: {'âœ… CÃ³' if inputs.get('use_bbg') else 'âŒ KhÃ´ng'}")
    lines.append(f"â€¢ <b>Compile KPM</b>: {'âœ… CÃ³' if inputs.get('use_kpm') else 'âŒ KhÃ´ng'}")
    lines.append(f"â€¢ <b>DÃ¹ng ZRAM</b>: {'âœ… CÃ³' if inputs.get('use_zram') else 'âŒ KhÃ´ng'}")
    # Cancel SUSFS logic is inverted from 'Báº­t SUSFS', check 'cancel_susfs'
    lines.append(f"â€¢ <b>Báº­t SUSFS</b>: {'âŒ KhÃ´ng' if inputs.get('cancel_susfs') else 'âœ… CÃ³'}")
    
    target_flags = []
    if inputs.get('build_a12_5_10'): target_flags.append('A12 (5.10)')
    if inputs.get('build_a13_5_15'): target_flags.append('A13 (5.15)')
    if inputs.get('build_a14_6_1'): target_flags.append('A14 (6.1)')
    if inputs.get('build_a15_6_6'): target_flags.append('A15 (6.6)')
    
    if inputs.get('build_all'):
        lines.append(f"â€¢ <b>PhiÃªn báº£n Android</b>: <code>Táº¥t cáº£ (A12-A15)</code>")
    elif target_flags:
        lines.append(f"â€¢ <b>PhiÃªn báº£n Android</b>: <code>{', '.join(target_flags)}</code>")
        
    sub_levels = inputs.get('sub_levels')
    if sub_levels:
        lines.append(f"â€¢ <b>Sub-versions (chá»‰ Ä‘á»‹nh)</b>: <code>{sub_levels.replace(',', ', ')}</code>")
    else:
        lines.append(f"â€¢ <b>Sub-versions</b>: <code>Táº¥t cáº£ cÃ¡c báº£n cáº­p nháº­t phá»¥</code>")
        
    # Táº¡o Inline Keyboard cho tin nháº¯n lÆ°u cáº¥u hÃ¬nh
    save_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ðŸŒ Xem trÃªn GitHub", url=f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}"),
            InlineKeyboardButton("ðŸ“¦ Táº£i file", url=f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}")
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
        return await q.answer("Lá»—i ID", show_alert=True)
        
    job = await storage.get_job_by_run_id(run_id)
    if not job:
        return await q.answer("KhÃ´ng tÃ¬m tháº¥y dá»¯ liá»‡u build nÃ y trong há»‡ thá»‘ng.", show_alert=True)
        
    try:
        if await send_saved_config(context.application, run_id, job, q.from_user.id):
            await q.answer("ÄÃ£ gá»­i tin nháº¯n cáº¥u hÃ¬nh vÃ o chat riÃªng cá»§a báº¡n! ðŸ“©", show_alert=True)
        else:
            await q.answer("KhÃ´ng cÃ³ thÃ´ng tin cáº¥u hÃ¬nh cho build nÃ y.", show_alert=True)
    except Exception as e:
        logger.error("Failed to PM user: %s", e)
        # NÃºt "LÆ°u" cÃ³ thá»ƒ Ä‘Æ°á»£c báº¥m trong nhÃ³m. Náº¿u user chÆ°a start bot, sáº½ nÃ©m lá»—i Forbidden
        await q.answer("âŒ Lá»—i: Báº¡n cáº§n nháº¯n tin cho Bot trÆ°á»›c (nháº¥n START) Ä‘á»ƒ nháº­n tin nháº¯n riÃªng.", show_alert=True)

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
        return await _send_msg(update, context, "âŒ ID khÃ´ng há»£p lá»‡.")
        
    gh: GitHubAPI = context.application.bot_data["gh"]
    msg = await _send_msg(update, context, f"â³ Äang gá»­i lá»‡nh há»§y Run #{run_id} lÃªn GitHub...")
    res = await gh.cancel_run(config.GKI_REPO, run_id)
    if res["status"] in (202, 204):
        for _ in range(10):
            await asyncio.sleep(5)
            rn = await gh.get_run(config.GKI_REPO, run_id)
            if rn["status"] == 200 and rn["json"].get("status") == "completed":
                break
        del_res = await gh.delete_run(config.GKI_REPO, run_id)
        if del_res["status"] == 204:
            await storage.delete_job_by_run_id(run_id)
            await msg.edit_text(f"âœ… ÄÃ£ há»§y vÃ  dá»n dáº¹p thÃ nh cÃ´ng Run #{run_id}.")
        else:
            await msg.edit_text(f"âš ï¸ Há»§y thÃ nh cÃ´ng nhÆ°ng xÃ³a tháº¥t báº¡i (HTTP {del_res['status']}).")
    else:
        await msg.edit_text(f"âŒ Lá»—i há»§y: {res['status']} {res.get('json', '')}")
        
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
        return await _send_msg(update, context, "âŒ ID khÃ´ng há»£p lá»‡.")
        
    gh: GitHubAPI = context.application.bot_data["gh"]
    
    msg = await _send_msg(update, context, f"â³ Äang gá»­i lá»‡nh xoÃ¡ Run #{run_id} lÃªn GitHub...")
    res = await gh.delete_run(config.GKI_REPO, run_id)
    
    if res["status"] in (202, 204):
        await msg.edit_text(f"âœ… ÄÃ£ yÃªu cáº§u xoÃ¡ thÃ nh cÃ´ng Run #{run_id}.")
        await storage.delete_job_by_run_id(run_id)
    else:
        if res["status"] in (404,):
            await storage.delete_job_by_run_id(run_id)
            await msg.edit_text(f"âœ… Run #{run_id} khÃ´ng tá»“n táº¡i trÃªn GitHub. ÄÃ£ xoÃ¡ khá»i dá»¯ liá»‡u ná»™i bá»™.")
        else:
            await msg.edit_text(f"âŒ Lá»—i xoÃ¡: {res['status']} {res.get('json', '')}")
            
    # XoÃ¡ tin nháº¯n sau 10s
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=10, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(_del_msg_job, when=10, chat_id=update.message.chat_id, data=update.message.message_id)

async def start_web_server(app_bot):
    """Khá»Ÿi cháº¡y API Web Server cá»¥c bá»™ phá»¥c vá»¥ Dashboard Realtime"""
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
    logger.info(f"âœ… Real-time Web Dashboard started natively on 0.0.0.0:{port}")


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

    # Background tasks sáº½ Ä‘Æ°á»£c cháº¡y trong _post_init

    # Owner-only commands
    app.add_handler(CommandHandler("start", cmd_start))
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
    app.add_handler(CallbackQueryHandler(cb_run_controls, pattern=r"^run:gki:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_run_control_action, pattern=r"^runctl:(cancel|close):gki:\d+(?::\d+)?$"))
    app.add_handler(CallbackQueryHandler(cb_save_run, pattern=r"^saverun:\d+$"))
    app.add_handler(MessageHandler(filters.Regex(r"^/cancel_\d+(?:@[\w_]+)?$"), cmd_cancel_run))
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
        conversation_timeout=300  # 5 phÃºt timeout trÃ¡nh conversation treo
    ))

    # OKI conversation
    app.add_handler(build_oki_conversation(gh, storage, config))

    async def _post_init(app_):
        # Seed dm_users tá»« lá»‹ch sá»­ jobs cÅ©
        seeded = await app_.bot_data["storage"].seed_dm_users_from_jobs()
        if seeded:
            logger.warning("Seeded %d DM users from job history", seeded)
        app_.create_task(app_.bot_data["storage"]._sync_with_cloud())
        app_.create_task(poller(app_))
        app_.create_task(start_web_server(app_))
    app.post_init = _post_init

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()


