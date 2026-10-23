import html
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "bot"

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp.web as aiohttp_web
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    constants,
)
from telegram.ext import (
    AIORateLimiter,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import config
from .backups import read_backup
from .buildsave import build_buildsave_conversation
from .catalog import load_downloads as _load_web_kernel_downloads
from .dispatch import dispatch_worker
from .github import GitHubAPI, match_workflow_run
from .github import parse_utc as _parse_utc
from .gki import SUB_LEVELS, _del_msg_job, build_gki_conversation
from .gki_contract import enabled, kpm_enabled, mode_label, susfs_enabled
from .maintenance import backup_worker, catalog_worker, cleanup_worker, cloud_worker
from .notifications import broadcast_worker, notification_worker
from .oki import build_oki_conversation
from .permissions import is_admin, is_owner
from .status import cached_run, status_worker
from .storage import HybridStorage
from .web_sync import _make_custom_id, get_realtime_data
from .workers import WorkerSupervisor, heartbeat

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.WARNING
)
logger = logging.getLogger("gww-bot")

from telegram.error import BadRequest

DATA_JSON = config.STORAGE_PATH


def tg_mention_html(user) -> str:
    name = user.first_name or user.username or "user"
    return f'<a href="tg://user?id={user.id}">{html.escape(str(name))}</a>'


def _html_user_mention(user_id, user_name) -> str:
    label = html.escape(str(user_name or user_id or "user"))
    try:
        return f'<a href="tg://user?id={int(user_id)}">{label}</a>'
    except (TypeError, ValueError):
        return label


def _dashboard_link(label="Dashboard"):
    if not config.DASHBOARD_URL:
        return ""
    return f"<a href='{html.escape(config.DASHBOARD_URL, quote=True)}'>{html.escape(label)}</a>"


def _start_background_task(app, coroutine, name):
    """Track long-lived workers separately from PTB's awaited handler tasks."""
    task = asyncio.create_task(coroutine, name=name)
    workers = app.bot_data.setdefault("background_tasks", set())
    workers.add(task)

    def finished(completed):
        workers.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            error = completed.exception()
            logger.error(
                "Background worker %s failed",
                name,
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(finished)
    return task


async def _notify_job(app, job, text, duplicates=()):
    """Queue completion deliveries; the notification worker owns retry state."""
    destinations = []
    for chat_id in dict.fromkeys(
        target for target in (job.get("chat_id"), job.get("user_id")) if target is not None
    ):
        destinations.append(
            dict(
                chat_id=chat_id,
                message_thread_id=job.get("message_thread_id")
                if chat_id == job.get("chat_id")
                else None,
            )
        )
    await app.bot_data["storage"].queue_notifications(
        [entry["_id"] for entry in (job, *duplicates)], destinations, text
    )
    return True


async def _bot_send_topic_safe(bot, chat_id: int, text: str, message_thread_id=None, **kwargs):
    """Send to a forum topic when possible, falling back if that topic is closed."""
    try:
        return await bot.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=text,
            **kwargs,
        )
    except BadRequest as e:
        if "Topic_closed" not in str(e) or message_thread_id is None:
            raise
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)


def _thread_id_from_update(update: Update):
    msg = update.effective_message if update else None
    return msg.message_thread_id if (msg and msg.is_topic_message) else None


async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def cmd_jobs(update, context):
    storage = context.application.bot_data["storage"]
    admin = await is_admin(update.effective_user.id, storage)
    jobs = [
        job
        for job in await storage.get_jobs()
        if (admin or job.get("user_id") == update.effective_user.id)
        and (job.get("status") != "completed" or job.get("conclusion") == "unknown")
    ]
    lines = ["📋 Hàng đợi / tiến trình của bot:"]
    for job in sorted(jobs, key=lambda value: value.get("_id", 0), reverse=True)[:20]:
        lines.append(
            f"#{job['_id']} · {job.get('type', 'gki').upper()} · {job.get('status', '?')} · Run {job.get('run_id') or 'chưa xác định'}"
        )
    if not jobs:
        lines.append("Không có job đang chờ/chạy.")
    lines.append("Hủy job: /canceljob ID")
    if admin:
        lines.append(
            "Job chưa rõ kết quả: /resolvejob ID RUN_ID hoặc /resolvejob ID refund (sau khi kiểm tra GitHub)."
        )
    await _send_msg(update, context, "\n".join(lines))


async def cmd_cancel_local_job(update, context):
    storage = context.application.bot_data["storage"]
    try:
        job_id = int(context.args[0])
        job = await storage.cancel_local_job(
            job_id,
            update.effective_user.id,
            admin=await is_admin(update.effective_user.id, storage),
        )
        text = (
            "Đã hủy trước khi gửi GitHub; lượt key đã hoàn nếu có."
            if job.get("status") == "completed"
            else "Đã ghi nhận yêu cầu hủy. Bot sẽ đối chiếu và hủy GitHub run khi xác định được."
        )
    except (ValueError, IndexError) as error:
        text = (
            str(error)
            if isinstance(error, ValueError) and context.args
            else "Cú pháp: /canceljob ID"
        )
    await _send_msg(update, context, text)


async def cmd_resolve_job(update, context):
    storage = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return
    try:
        job_id, value = int(context.args[0]), context.args[1]
        if value != "refund":
            run_id = int(value)
            job = next((job for job in await storage.get_jobs() if job.get("_id") == job_id), None)
            if not job:
                raise ValueError("Không tìm thấy job.")
            result = await context.application.bot_data["gh"].get_run(job["repo"], run_id)
            run = result.get("json") or {}
            if (
                result.get("status") != 200
                or run.get("head_branch") != job.get("ref")
                or str(run.get("path", "")).split("@", 1)[0].rsplit("/", 1)[-1]
                != job.get("workflow_file")
            ):
                raise ValueError("Run không tồn tại hoặc không khớp workflow/branch của job.")
            await storage.resolve_job(job_id, run_id=run_id)
        else:
            await storage.resolve_job(job_id, refund=True)
        text = f"✅ Đã xử lý job #{job_id}."
    except (ValueError, IndexError) as error:
        text = (
            str(error)
            if isinstance(error, ValueError) and len(context.args) >= 2
            else "Cú pháp: /resolvejob ID RUN_ID|refund"
        )
    await _send_msg(update, context, text)


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
        success_count = sum(
            1 for j in jobs if j.get("status") == "completed" and j.get("conclusion") == "success"
        )
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
                    short_vers.append(v[len(prefix) :])
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
                    "❌ <b>Đã huỷ/thất bại toàn bộ batch!</b>",
                    f"🔨 {variant} — {v_str}",
                    f"⏳ Tiến trình: {completed_count}/{total_count}",
                ]
            else:
                lines = [
                    "✅ <b>Đã hoàn thành batch build!</b>",
                    f"🔨 {variant} — {v_str}",
                    f"⏳ Tiến trình: {completed_count}/{total_count} ({success_count} thành công)",
                ]
        else:
            lines = [
                "✅ <b>Đang build kernel cho lưu trữ!</b>",
                f"🔨 {variant} — {v_str}",
                f"⏳ Tiến trình: {completed_count}/{total_count}",
            ]

        from . import config

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

        lines.append(
            f"<blockquote>Xem: <a href='{github_url}'>Github</a> | {_dashboard_link()} ❞</blockquote>"
        )

        text = "\n".join(lines)

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        if "is not modified" not in str(e) and "not found" not in str(e).lower():
            logger.error(f"update_batch_message error: {e}")


async def update_rebuild_message(rebuild_msg_id: str, storage, bot):
    """Cập nhật tin nhắn rebuild tiến trình khi có job rebuild_queued hoặc complete."""
    try:
        # Format mới: "{msg_id}|{chat_id}|{custom_id}" (dùng | để tránh clash với chat_id âm)
        # Format cũ:  "{msg_id}-{chat_id}" hoặc "{msg_id}--{abs_chat_id}"
        if "|" in rebuild_msg_id:
            parts = rebuild_msg_id.split("|")
            if len(parts) < 2:
                return
            msg_id_int = int(parts[0])
            chat_id_int = int(parts[1])
            custom_id = parts[2] if len(parts) > 2 else ""
        else:
            # Format cũ: lấy phần đầu là msg_id, phần còn lại (kể cả dấu âm) là chat_id
            dash_idx = rebuild_msg_id.index("-")
            msg_id_int = int(rebuild_msg_id[:dash_idx])
            chat_id_int = int(rebuild_msg_id[dash_idx + 1 :])
            custom_id = ""

        all_jobs = await storage.get_jobs()
        rjobs = [
            j
            for j in all_jobs
            if j.get("rebuild_msg_id") == rebuild_msg_id and j.get("type") == "buildsave"
        ]
        if not rjobs:
            return

        variant = rjobs[0].get("bs_variant", "")
        total = len(rjobs)
        done = sum(1 for j in rjobs if j.get("status") == "completed")
        success = sum(
            1 for j in rjobs if j.get("status") == "completed" and j.get("conclusion") == "success"
        )

        # Lấy run_id của job đang chạy để taoj link Github động
        active_run_id = None
        for rj in rjobs:
            if rj.get("status") in ("dispatched", "in_progress", "running") and rj.get("run_id"):
                active_run_id = rj.get("run_id")
                break
        repo_name = rjobs[0].get("repo", config.GKI_REPO) if rjobs else config.GKI_REPO
        if active_run_id:
            github_url = (
                f"https://github.com/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{active_run_id}"
            )
        else:
            github_url = f"https://github.com/{config.GITHUB_OWNER}/{repo_name}/actions"

        link_line = f"<blockquote>Xem: <a href='{github_url}'><b>Github</b></a> | {_dashboard_link()}</blockquote>"

        if done == total:
            text = (
                f"\u2705 <b>Rebuild ho\u00e0n t\u1ea5t! {custom_id}</b>\n"
                f"<b>\u23f3 Ti\u1ebfn tr\u00ecnh: {done}/{total} ({success} th\u00e0nh c\u00f4ng)</b>\n"
                + link_line
            )
        else:
            text = (
                f"\ud83d\udd04 <b>\u0110ang Rebuild... {custom_id}</b>\n"
                f"<b>\u23f3 Ti\u1ebfn tr\u00ecnh: {done}/{total}</b>\n" + link_line
            )

        await bot.edit_message_text(
            text=text,
            chat_id=chat_id_int,
            message_id=msg_id_int,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        if "is not modified" not in str(e):
            logger.error(f"update_rebuild_message error: {e}")


async def poller(app):
    gh = app.bot_data["gh"]
    storage = app.bot_data["storage"]
    while True:
        heartbeat(app, "poller")
        try:
            # 2. Polling for unnotified jobs
            jobs = await storage.list_unnotified_jobs()
            for job in jobs:
                heartbeat(app, "poller")
                try:
                    # Re-check state: the snapshot may contain a duplicate job
                    # already notified or a batch cancelled earlier this cycle.
                    current_jobs = await storage.get_jobs()
                    job = next(
                        (entry for entry in current_jobs if entry.get("_id") == job.get("_id")),
                        None,
                    )
                    if job is None or job.get("notified") or job.get("notification_enqueued"):
                        continue
                    if job.get("status") in ("queued", "rebuild_queued"):
                        continue
                    if job["_id"] in app.bot_data.get("dispatch_inflight", set()):
                        continue
                    if job.get("completion_text"):
                        await _notify_job(app, job, job["completion_text"])
                        if job.get("batch_id"):
                            await update_batch_message(job["batch_id"], storage, app.bot)
                        if job.get("rebuild_msg_id"):
                            await update_rebuild_message(job["rebuild_msg_id"], storage, app.bot)
                        continue

                    repo = job["repo"]
                    run_id = job.get("run_id")
                    ref = job.get("ref", "main")
                    workflow_file = job.get("workflow_file")
                    job_created_at_iso = job.get("dispatch_started_at") or job.get("created_at")

                    runs_resp = None
                    if not run_id and ref and workflow_file and job_created_at_iso:
                        runs_resp = await gh.list_runs_for_repo(
                            repo, ref, job_created_at_iso, workflow_file
                        )
                        if runs_resp["status"] == 200:
                            all_jobs = await storage.get_jobs()
                            best_match = match_workflow_run(
                                job, runs_resp["json"].get("workflow_runs", []), all_jobs
                            )
                            if best_match:
                                run_id = await storage.link_reconciled_run(
                                    job["_id"], best_match["id"], job.get("dispatch_generation")
                                )

                    if not run_id:
                        if (
                            runs_resp
                            and runs_resp["status"] == 200
                            and job_created_at_iso
                            and datetime.now(timezone.utc) - _parse_utc(job_created_at_iso)
                            > timedelta(minutes=20)
                        ):
                            await storage.update_job(
                                job["_id"],
                                {
                                    "status": "completed",
                                    "conclusion": "unknown",
                                    "error": "No matching GitHub workflow run was found within 20 minutes",
                                },
                            )
                            await _notify_job(
                                app,
                                job,
                                "⚠️ Chưa xác định được GitHub run sau 20 phút. Bot không tự gửi lại để tránh build trùng; hãy nhờ owner kiểm tra. Lượt key đang giữ sẽ không tự hoàn khi kết quả còn chưa rõ.",
                            )
                            await storage.queue_notice(
                                f"unknown-job:{job['_id']}:{job.get('dispatch_generation', '')}",
                                config.OWNER_ID,
                                f"⚠️ Job #{job['_id']} chưa xác định được run. Kiểm tra GitHub rồi dùng /resolvejob {job['_id']} RUN_ID hoặc /resolvejob {job['_id']} refund nếu chắc chắn không có build.",
                            )
                            if job.get("batch_id"):
                                await update_batch_message(job["batch_id"], storage, app.bot)
                            if job.get("rebuild_msg_id"):
                                await update_rebuild_message(
                                    job["rebuild_msg_id"], storage, app.bot
                                )
                        continue

                    if job.get("pending_key"):
                        if not await storage.consume_job_key(job["_id"]):
                            logger.warning(
                                "Deferred key charge could not be completed for job %s", job["_id"]
                            )
                    await storage.commit_reservation(job["_id"])

                    if job.get("cancel_requested"):
                        await gh.cancel_run(repo, int(run_id))
                    cached = (
                        job.get("github_result")
                        if job.get("github_completed")
                        else cached_run(app, repo, run_id)
                    )
                    rn = (
                        {"status": 200, "json": cached}
                        if cached
                        else await gh.get_run(repo, int(run_id))
                    )

                    if rn["status"] == 404:
                        logger.warning("Run %s not found (deleted?), marking cancelled", run_id)
                        await storage.update_job(
                            job["_id"],
                            {"status": "completed", "conclusion": "cancelled", "notified": True},
                        )
                        if job.get("batch_id"):
                            await update_batch_message(job["batch_id"], storage, app.bot)
                        if job.get("rebuild_msg_id"):
                            await update_rebuild_message(job["rebuild_msg_id"], storage, app.bot)
                        continue
                    elif rn["status"] != 200:
                        continue

                    status = rn["json"].get("status")
                    if status != "completed":
                        local_status = "in_progress" if status == "in_progress" else "dispatched"
                        if job.get("status") != local_status:
                            await storage.update_job(job["_id"], {"status": local_status})
                    if status == "completed":
                        duplicate_jobs = []
                        if run_id:
                            try:
                                all_jobs = await storage.get_jobs()
                                duplicate_jobs = [
                                    j
                                    for j in all_jobs
                                    if j.get("_id") != job.get("_id")
                                    and j.get("repo") == job.get("repo")
                                    and j.get("chat_id") == job.get("chat_id")
                                    and j.get("user_id") == job.get("user_id")
                                    and str(j.get("run_id")) == str(run_id)
                                    and not j.get("notified")
                                    and not j.get("notification_enqueued")
                                ]
                            except Exception as e:
                                logger.warning(
                                    "Duplicate job lookup failed for run_id=%s: %s", run_id, e
                                )
                        conclusion = rn["json"].get("conclusion") or "unknown"
                        html_url = rn["json"].get("html_url")

                        gh_dur = ""
                        try:
                            t1_str = rn["json"].get("run_started_at") or rn["json"].get(
                                "created_at", ""
                            )
                            if t1_str:
                                t1 = datetime.fromisoformat(t1_str.replace("Z", "+00:00"))
                                t2_str = rn["json"].get("updated_at")
                                if t2_str:
                                    t2 = datetime.fromisoformat(t2_str.replace("Z", "+00:00"))
                                    diff = int((t2 - t1).total_seconds())
                                    if diff > 0:
                                        gh_dur = f"{diff // 60}m {diff % 60}s"
                        except Exception:
                            pass

                        user_id = job.get("user_id")
                        user_name = job.get("user_name", "")
                        if not user_name:
                            user_name = str(user_id)
                        mention = _html_user_mention(user_id, user_name)
                        icon = (
                            "✅"
                            if conclusion == "success"
                            else "❌"
                            if conclusion == "failure"
                            else "⚠️"
                        )

                        created_at_dt = _parse_utc(
                            job.get("created_at", datetime.now(timezone.utc).isoformat())
                        )
                        elapsed = int(
                            (datetime.now(timezone.utc) - created_at_dt).total_seconds() // 60
                        )

                        download_link = f"https://nightly.link/{config.GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
                        github_link = html.escape(str(html_url or "#"), quote=True)
                        download_link = html.escape(str(download_link), quote=True)
                        text = (
                            f"<b>{icon} Build {job.get('type', '?').upper()} kết thúc!\n"
                            f"┃\n"
                            f"┠ Người nhận: {mention}\n"
                            f"┠ Trạng thái: {conclusion.upper()}\n"
                            f"┖ Thời gian: {elapsed} phút\n"
                            f"<blockquote>Xem thông tin: "
                            f"<a href='{github_link}'>Github</a>┃"
                            f"{_dashboard_link()}┃"
                            f"<a href='{download_link}'>Download</a></blockquote></b>"
                        )

                        # Persist the terminal GitHub state before attempting a
                        # Telegram delivery. A transient Telegram outage must
                        # not leave the dashboard showing a completed run as
                        # "building".
                        completion_fields = {
                            "status": "completed",
                            "conclusion": conclusion,
                            "gh_duration": gh_dur,
                            "catalog_pending": conclusion == "success"
                            and job.get("type") in ("gki", "buildsave"),
                            "cancel_requested": False,
                            "github_completed": True,
                            "github_result": {
                                key: rn["json"].get(key)
                                for key in (
                                    "status",
                                    "conclusion",
                                    "html_url",
                                    "run_started_at",
                                    "created_at",
                                    "updated_at",
                                )
                            },
                        }
                        await storage.update_job(job["_id"], completion_fields)
                        for dup in duplicate_jobs:
                            await storage.update_job(dup["_id"], completion_fields)
                        if job.get("type") == "buildsave" and job.get("batch_id"):
                            await update_batch_message(job["batch_id"], storage, app.bot)
                        if job.get("rebuild_msg_id"):
                            await update_rebuild_message(job["rebuild_msg_id"], storage, app.bot)

                        # Catalog/history must progress even when Telegram is
                        # unavailable. These writes are safe to repeat on retry.
                        if conclusion == "success":
                            await storage.add_successful_build(
                                run_id, user_id, job.get("ref", "unknown"), user_name
                            )

                        if not await _notify_job(app, job, text, duplicate_jobs):
                            logger.warning(
                                "Completion notification will be retried for job %s", job["_id"]
                            )
                except Exception as e:
                    logger.error("Job %s error: %s", job.get("_id"), e, exc_info=True)
        except Exception as e:
            logger.error("Poller error: %s", e, exc_info=True)

        await asyncio.sleep(config.POLL_INTERVAL)


async def _send_msg(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    kwargs.pop("quote", None)
    chat_id = update.effective_chat.id
    thread_id = (
        update.effective_message.message_thread_id
        if (update.effective_message and update.effective_message.is_topic_message)
        else None
    )
    return await _bot_send_topic_safe(
        context.bot,
        chat_id,
        text,
        message_thread_id=thread_id,
        **kwargs,
    )


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
                await _send_msg(
                    update,
                    context,
                    f"🗑️ Đã xoá key <code>{code}</code>.",
                    parse_mode=constants.ParseMode.HTML,
                )
            else:
                await _send_msg(
                    update,
                    context,
                    f"⚠️ Key <code>{code}</code> không tồn tại.",
                    parse_mode=constants.ParseMode.HTML,
                )
            return
        else:
            uses = int(action)
    except Exception:
        return await _send_msg(update, context, "Cú pháp: /key {mã} {số_lượt|delete}")

    await storage.set_key(code, uses, vip=False)
    await _send_msg(
        update,
        context,
        f"✅ Đã set key <code>{code}</code> với {uses} lượt.",
        parse_mode=constants.ParseMode.HTML,
    )


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
    await _send_msg(
        update,
        context,
        f"💎 Đã tạo VIP key <code>{code}</code> với {uses} lượt (không giới hạn 1h).",
        parse_mode=constants.ParseMode.HTML,
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
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{update.message.message_id}")]]
    )
    await _send_msg(
        update, context, "\n".join(lines), parse_mode=constants.ParseMode.HTML, reply_markup=kb
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        import time

        start_time = time.time()
        msg = await _send_msg(
            update,
            context,
            "🏓 <b>Ping...</b>",
            parse_mode=constants.ParseMode.HTML,
        )
        end_time = time.time()
        ping_ms = int((end_time - start_time) * 1000)

        if msg:
            await msg.edit_text(
                f"<b>🏓 Pong!\n<blockquote>Độ trễ: {ping_ms}ms</blockquote></b>",
                parse_mode=constants.ParseMode.HTML,
            )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if chat and user and chat.type == "private":
        await storage.track_dm_user(user.id, chat.id)
    await _send_msg(
        update,
        context,
        "<b>✅ Đã kích hoạt DM!<blockquote>Bây giờ bạn có thể nhận thông báo hoàn tất qua tin nhắn riêng.</blockquote></b>",
        parse_mode=constants.ParseMode.HTML,
    )


async def ensure_user_started_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type == "private":
        return True

    storage: HybridStorage = context.application.bot_data["storage"]
    if await storage.has_dm_user(user.id):
        return True

    bot_username = (await context.bot.get_me()).username
    start_url = f"https://t.me/{bot_username}?start=dm"
    mention = _html_user_mention(user.id, user.full_name)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Start bot", url=start_url)]])
    m = await _send_msg(
        update,
        context,
        f"<b><blockquote>🔐 {mention}, bấm Start bot trước rồi dùng lại lệnh nha!</blockquote></b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=kb,
    )
    if m:
        context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
    return False


async def dm_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all: lưu user DM và nhóm mà bot đang hoạt động."""
    chat = update.effective_chat
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if chat and user:
        if chat.type == "private":
            await storage.track_dm_user(user.id, chat.id)
        elif chat.type in ("group", "supergroup"):
            await storage.track_group(chat.id, chat.title or "")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tạo broadcast và yêu cầu admin chọn DM, nhóm hoặc cả hai."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not user or not await is_admin(user.id, storage):
        return

    replied = update.message.reply_to_message if update.message else None
    text_body = " ".join(context.args) if context.args else ""
    if not replied and not text_body:
        await _send_msg(
            update,
            context,
            "📌 <b>Cách dùng:</b>\n"
            "• <code>/bc Nội dung thông báo</code>\n"
            "• Reply một tin nhắn + gõ <code>/bc</code> hoặc <code>/broadcast</code>",
            parse_mode=constants.ParseMode.HTML,
        )
        return

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1️⃣ DM", callback_data="broadcast:dm"),
                InlineKeyboardButton("2️⃣ Nhóm", callback_data="broadcast:group"),
                InlineKeyboardButton("3️⃣ Cả hai", callback_data="broadcast:all"),
            ]
        ]
    )
    status = await _send_msg(
        update,
        context,
        "📢 <b>Chọn đối tượng nhận Broadcast:</b>",
        parse_mode=constants.ParseMode.HTML,
        reply_markup=kb,
    )
    if not status:
        return

    pending = context.application.bot_data.setdefault("pending_broadcasts", {})
    pending[(status.chat_id, status.message_id)] = {
        "admin_id": user.id,
        "text": text_body,
        "source_message": replied,
    }


async def _send_broadcast_content(bot, chat_id: int, payload: dict):
    """Gửi lại nội dung thành tin mới, không phụ thuộc message_id và không lộ nguồn."""
    source = payload.get("source_message")
    if isinstance(source, dict):
        source = Message.de_json(source, bot)
    if source is None:
        return await bot.send_message(chat_id=chat_id, text=payload["text"])

    common_caption = {
        "caption": source.caption,
        "caption_entities": source.caption_entities,
    }
    if source.text is not None:
        return await bot.send_message(
            chat_id=chat_id,
            text=source.text,
            entities=source.entities,
        )
    if source.photo:
        return await bot.send_photo(
            chat_id=chat_id, photo=source.photo[-1].file_id, **common_caption
        )
    if source.video:
        return await bot.send_video(
            chat_id=chat_id,
            video=source.video.file_id,
            duration=source.video.duration,
            width=source.video.width,
            height=source.video.height,
            supports_streaming=source.video.supports_streaming,
            **common_caption,
        )
    if source.document:
        return await bot.send_document(
            chat_id=chat_id, document=source.document.file_id, **common_caption
        )
    if source.audio:
        return await bot.send_audio(
            chat_id=chat_id,
            audio=source.audio.file_id,
            duration=source.audio.duration,
            performer=source.audio.performer,
            title=source.audio.title,
            **common_caption,
        )
    if source.animation:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=source.animation.file_id,
            duration=source.animation.duration,
            width=source.animation.width,
            height=source.animation.height,
            **common_caption,
        )
    if source.voice:
        return await bot.send_voice(
            chat_id=chat_id,
            voice=source.voice.file_id,
            duration=source.voice.duration,
            **common_caption,
        )
    if source.video_note:
        return await bot.send_video_note(
            chat_id=chat_id,
            video_note=source.video_note.file_id,
            duration=source.video_note.duration,
            length=source.video_note.length,
        )
    if source.sticker:
        return await bot.send_sticker(chat_id=chat_id, sticker=source.sticker.file_id)
    if source.contact:
        return await bot.send_contact(
            chat_id=chat_id,
            phone_number=source.contact.phone_number,
            first_name=source.contact.first_name,
            last_name=source.contact.last_name,
            vcard=source.contact.vcard,
        )
    if source.location:
        return await bot.send_location(
            chat_id=chat_id,
            latitude=source.location.latitude,
            longitude=source.location.longitude,
        )

    return await bot.copy_message(
        chat_id=chat_id,
        from_chat_id=source.chat_id,
        message_id=source.message_id,
    )


async def cb_broadcast_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phát nội dung theo phạm vi đã chọn và cập nhật một status message."""
    query = update.callback_query
    if not query or not query.message or not query.from_user:
        return

    key = (query.message.chat_id, query.message.message_id)
    pending = context.application.bot_data.setdefault("pending_broadcasts", {})
    payload = pending.get(key)
    if not payload:
        await query.answer("Yêu cầu này đã hết hạn hoặc đã được xử lý.", show_alert=True)
        return
    if query.from_user.id != payload["admin_id"]:
        await query.answer("Chỉ admin tạo Broadcast này mới được chọn.", show_alert=True)
        return

    # Pop trước khi chạy để một cú double-click không tạo hai broadcast.
    pending.pop(key, None)
    await query.answer()

    scope = query.data.rsplit(":", 1)[-1]
    storage: HybridStorage = context.application.bot_data["storage"]

    admin_ids = {config.OWNER_ID, *config.ADMIN_IDS}
    admin_ids.update(await storage.get_admin_ids())
    dm_targets = []
    group_targets = []
    if scope in ("dm", "all"):
        dm_targets = [
            u
            for u in await storage.get_dm_users()
            if u.get("user_id") not in admin_ids and u.get("chat_id")
        ]
    if scope in ("group", "all"):
        group_targets = [g for g in await storage.get_group_chats() if g.get("chat_id")]

    targets = [("dm", u.get("chat_id"), u.get("user_id")) for u in dm_targets]
    targets.extend(("group", g.get("chat_id"), None) for g in group_targets)
    total = len(targets)
    labels = {"dm": "DM", "group": "Nhóm", "all": "Cả hai"}

    source = payload.get("source_message")
    payload = {**payload, "source_message": json.loads(source.to_json()) if source else None}
    batch_id = f"{query.message.chat_id}:{query.message.message_id}"
    await storage.queue_broadcast(
        batch_id,
        payload,
        [chat_id for _, chat_id, _ in targets],
        query.message.chat_id,
        query.message.message_id,
    )
    await query.message.edit_text(
        f"⏳ Đã xếp hàng Broadcast tới {total} chat. Tiến trình sẽ tự cập nhật."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hien danh sach lenh huong dan."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    admin = await is_admin(user.id, storage)

    text = (
        "📖 <b>Danh sách lệnh Bot</b>\n\n"
        "📌 <b>User:</b>\n"
        "<blockquote>"
        "<b>/gki</b> <code>&lt;key&gt;</code> — Build GKI Kernel\n"
        "<b>/oki</b> <code>&lt;key&gt;</code> — Build OKI Kernel\n"
        "<b>/dl</b> <code>&lt;version&gt;</code> — Lấy link tải Kernel\n"
        "<b>/jobs</b> — Xem hàng đợi và tiến trình của bạn\n"
        "<b>/canceljob</b> <code>ID</code> — Hủy job của bạn\n"
        "<b>/ping</b> — Kiểm tra bot hoạt động\n"
        "<b>/help</b> — Hiện hướng dẫn này\n"
        "</blockquote>"
    )

    if admin:
        text += (
            "\n🔒 <b>Admin:</b>\n"
            "<blockquote>"
            "<b>/build</b> — Build Kernel lưu trữ trên Web\n"
            "<b>/key</b> <code>&lt;code&gt; &lt;uses|delete&gt;</code> — Tạo/sửa/xoá key\n"
            "<b>/keyvip</b> <code>&lt;code&gt; &lt;uses&gt;</code> — Tạo VIP key\n"
            "<b>/keys</b> — Xem danh sách key\n"
            "<b>/st</b> — Xem build đang chạy\n"
            "<b>/list</b> — Lịch sử build thành công\n"
            "<b>/resolvejob</b> <code>ID RUN_ID|refund</code> — Xử lý job chưa rõ kết quả sau khi kiểm tra GitHub\n"
            "<b>/rb</b> hoặc <b>/rebuild</b> — Build lại run đã lưu\n"
            "<b>/data</b> — Khôi phục data từ JSON\n"
            "<b>/bc</b> hoặc <b>/broadcast</b> — Broadcast thông báo\n"
            "</blockquote>"
        )

    if config.DASHBOARD_URL:
        text += "\n🌐 " + _dashboard_link()

    msg = await _send_msg(
        update, context, text, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True
    )
    if context.job_queue:
        context.job_queue.run_once(
            _del_msg_job, when=60, chat_id=update.effective_chat.id, data=msg.message_id
        )
        if update.message:
            context.job_queue.run_once(
                _del_msg_job,
                when=60,
                chat_id=update.effective_chat.id,
                data=update.message.message_id,
            )


async def cmd_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message_to_edit=None, cmd_msg_id=0
):
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
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔄 Làm mới", callback_data=f"refresh_st:{cmd_msg_id}"),
                    InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{cmd_msg_id}"),
                ]
            ]
        )
        if message_to_edit:
            try:
                await message_to_edit.edit_text(text, reply_markup=kb)
            except Exception:
                pass
        else:
            m = await _send_msg(update, context, text, reply_markup=kb)
            if context.job_queue:
                context.job_queue.run_once(
                    _del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id
                )
                if update.message:
                    context.job_queue.run_once(
                        _del_msg_job, when=60, chat_id=update.message.chat_id, data=cmd_msg_id
                    )
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
        mention = (
            _html_user_mention(user_id, user_name) if user_id != "Unknown" else "GitHub / Manual"
        )

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
                lines.append(
                    f"┠ ⏳ Hàng chờ: <code>{', '.join(wv)}</code>"
                    + (f" (+{len(q_jobs) - 5})" if len(q_jobs) > 5 else "")
                )
            lines.append(f"┠ Huỷ sub này → /cancel_{run_id}")
            lines.append(f"┖ Huỷ TOÀN BỘ batch → /cancelbatch_{batch_id.replace('-', '')[:16]}")
        else:
            lines.append(f"┖ Huỷ job → /cancel_{run_id}")

    # Hiển thị các batch đang chờ chưa chạy
    for bid, bj_list in batch_queued.items():
        if bid not in seen_batches and bj_list:
            bj_list = sorted(bj_list, key=lambda x: x.get("batch_index", 0))
            vers = [j.get("bs_full_ver", "?") for j in bj_list[:5]]
            lines.append("")
            lines.append("<b>🗂 [Web Build — Hàng chờ]</b>")
            lines.append(
                f"┠ ⏳ {', '.join(vers)}" + (f" (+{len(bj_list) - 5})" if len(bj_list) > 5 else "")
            )
            lines.append(f"┖ Huỷ TOÀN BỘ → /cancelbatch_{bid.replace('-', '')[:16]}")

    msg_text = "\n".join(lines)
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Làm mới", callback_data=f"refresh_st:{cmd_msg_id}"),
                InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{cmd_msg_id}"),
            ]
        ]
    )
    if message_to_edit:
        try:
            await message_to_edit.edit_text(
                msg_text, parse_mode=constants.ParseMode.HTML, reply_markup=kb
            )
        except Exception:
            pass
    else:
        await _send_msg(
            update, context, msg_text, parse_mode=constants.ParseMode.HTML, reply_markup=kb
        )


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


async def show_list_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 1,
    message_to_edit=None,
    cmd_msg_id=0,
):
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

    text = "🗂 <b>Danh sách các bản build GKI:</b>\n\n"

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
                mention = _html_user_mention(user_id, user_name)
        else:
            actor = r.get("actor", {}).get("login", "Unknown")
            mention = f"GitHub / {actor}"

        text += f"<b>{start_idx + i + 1}. Run #{run_id}</b> by {mention}\n"
        text += f"Time: {time_str}\n"
        text += f"Xoá: /delete_{run_id}\n"
        text += f"<blockquote><b>Xem : <a href='{html_url}'>Github</a> | <a href='{nightly_url}'>File</a> | {_dashboard_link()}</b></blockquote>\n\n"

    kb = []
    if total_pages > 1:
        kb.append(
            [
                InlineKeyboardButton("⬅️ Trước", callback_data=f"listpage:{page - 1}"),
                InlineKeyboardButton(f"{page}/{total_pages}", callback_data="none"),
                InlineKeyboardButton("Sau ➡️", callback_data=f"listpage:{page + 1}"),
            ]
        )
    kb.append([InlineKeyboardButton("❌ Đóng", callback_data=f"closemsg:{cmd_msg_id}")])
    reply_markup = InlineKeyboardMarkup(kb)
    await message_to_edit.edit_text(
        text,
        parse_mode=constants.ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


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

    variant = inputs.get("kernelsu_variant", inputs.get("variant"))
    branch_raw = inputs.get("branch")
    branch = (
        mode_label(inputs)
        if "kernelsu_variant" in inputs
        else (branch_raw.split("(")[0] if branch_raw else None)
    )
    c_name = inputs.get("version", inputs.get("custom_name", ""))
    has_zram = "ZRAM" if enabled(inputs.get("use_zram")) else ""
    has_bbg = "BBG" if enabled(inputs.get("use_bbg")) else ""
    has_kpm = "KPM" if kpm_enabled(inputs) else ""
    has_susfs = "SUSFS" if susfs_enabled(inputs) else ""
    ksu = inputs.get("ksu_type", "")

    parts = []
    for x in [variant, branch, c_name, has_zram, has_bbg, has_kpm, has_susfs, ksu]:
        if x:
            parts.append(str(x).strip())

    prefix = "|".join(parts)

    subs = inputs.get("sub_levels", "")
    sub_list = subs.split(",") if subs else []

    for tk, prefix_tk in [
        ("build_a12_5_10", "A12-5.10"),
        ("build_a13_5_15", "A13-5.15"),
        ("build_a14_6_1", "A14-6.1"),
        ("build_a15_6_6", "A15-6.6"),
        ("build_a16_6_12", "A16-6.12"),
    ]:
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
    await show_list_page(
        update, context, page=page, message_to_edit=q.message, cmd_msg_id=cmd_msg_id
    )


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
        [
            InlineKeyboardButton("Hủy bỏ", callback_data=f"runctl:cancel:{repo_tag}:{run_id}"),
            InlineKeyboardButton("Đóng", callback_data=f"runctl:close:{repo_tag}:{run_id}"),
        ],
        [InlineKeyboardButton("Xem", url=view_url)],
    ]
    await q.edit_message_text(text=f"Run #{run_id} — GKI", reply_markup=InlineKeyboardMarkup(kb))


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
        await q.edit_message_text(
            f"⏳ Đã gửi lệnh hủy run #{run_id}.\nĐang chờ xác nhận từ GitHub..."
        )

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
                            parse_mode="HTML",
                        )
                    else:
                        await q.edit_message_text(
                            f"ℹ️ Run #{run_id} đã hoàn tất với kết quả: <b>{conclusion}</b>",
                            parse_mode="HTML",
                        )
                    # Xóa tin nhắn lệnh gốc
                    if cmd_msg_id:
                        try:
                            await context.bot.delete_message(
                                chat_id=q.message.chat_id, message_id=cmd_msg_id
                            )
                        except Exception:
                            pass
                    # Tự xóa sau 60s
                    if context.job_queue:
                        context.job_queue.run_once(
                            _del_msg_job,
                            when=60,
                            chat_id=q.message.chat_id,
                            data=q.message.message_id,
                        )
                    return

        # Timeout - vẫn chưa cancelled sau 60s
        await q.edit_message_text(
            f"⚠️ Đã gửi lệnh hủy run #{run_id} nhưng chưa xác nhận được.\n"
            f"Vui lòng kiểm tra trên GitHub.",
            parse_mode="HTML",
        )
        if context.job_queue:
            context.job_queue.run_once(
                _del_msg_job, when=60, chat_id=q.message.chat_id, data=q.message.message_id
            )

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
            m = await update.message.reply_text(
                f"⚠️ Chỉ được 1 job/1h. Vui lòng đợi {remaining} phút.", quote=False
            )
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
            dt = (
                datetime.fromisoformat(job_created_at)
                .replace(tzinfo=timezone.utc)
                .astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            )
            lines.append(f"🕒 <b>Ngày build</b>: <code>{dt.strftime('%H:%M %d/%m/%Y')}</code>\n")
        except:
            pass
    else:
        lines.append("🕒 <b>Ngày build</b>: <code>Chưa rõ</code>\n")

    lines.append(
        f"• <b>KernelSU Variant</b>: <code>{inputs.get('kernelsu_variant', 'None')}</code>"
    )
    lines.append(f"• <b>Chế độ KSU/SUSFS</b>: <code>{mode_label(inputs)}</code> (branch tự động)")

    if inputs.get("version"):
        lines.append(f"• <b>Version Custom</b>: <code>{inputs.get('version')}</code>")

    lines.append(f"• <b>Compile BBG</b>: {'✅ Có' if inputs.get('use_bbg') else '❌ Không'}")
    lines.append(f"• <b>Compile KPM</b>: {'✅ Có' if kpm_enabled(inputs) else '❌ Không'}")
    lines.append(f"• <b>Dùng ZRAM</b>: {'✅ Có' if inputs.get('use_zram') else '❌ Không'}")
    # Native mode and legacy cancel_susfs use the same shared semantics.
    lines.append(f"• <b>Bật SUSFS</b>: {'✅ Có' if susfs_enabled(inputs) else '❌ Không'}")
    for key, label in (
        ("use_nomount", "NoMount"),
        ("use_networking", "Networking"),
        ("droidspaces_ntsync", "NTSync"),
        ("cve_2026_43499_patch", "CVE-2026-43499"),
    ):
        lines.append(f"• <b>{label}</b>: {'✅ Có' if enabled(inputs.get(key)) else '❌ Không'}")

    target_flags = []
    if inputs.get("build_a12_5_10"):
        target_flags.append("A12 (5.10)")
    if inputs.get("build_a13_5_15"):
        target_flags.append("A13 (5.15)")
    if inputs.get("build_a14_6_1"):
        target_flags.append("A14 (6.1)")
    if inputs.get("build_a15_6_6"):
        target_flags.append("A15 (6.6)")
    if inputs.get("build_a16_6_12"):
        target_flags.append("A16 (6.12)")

    if inputs.get("build_all"):
        lines.append("• <b>Phiên bản Android</b>: <code>Tất cả (A12-A16)</code>")
    elif target_flags:
        lines.append(f"• <b>Phiên bản Android</b>: <code>{', '.join(target_flags)}</code>")

    sub_levels = inputs.get("sub_levels")
    if sub_levels:
        lines.append(
            f"• <b>Sub-versions (chỉ định)</b>: <code>{sub_levels.replace(',', ', ')}</code>"
        )
    else:
        lines.append("• <b>Sub-versions</b>: <code>Tất cả các bản cập nhật phụ</code>")

    # Tạo Inline Keyboard cho tin nhắn lưu cấu hình
    save_kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🌐 Xem trên GitHub",
                    url=f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}",
                ),
                InlineKeyboardButton(
                    "📦 Tải file",
                    url=f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}",
                ),
            ]
        ]
    )

    await app.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode=constants.ParseMode.HTML,
        reply_markup=save_kb,
        disable_web_page_preview=True,
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
        await q.answer(
            "❌ Lỗi: Bạn cần nhắn tin cho Bot trước (nhấn START) để nhận tin nhắn riêng.",
            show_alert=True,
        )


async def cmd_cancel_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Huỷ toàn bộ batch buildsave theo batch_id."""
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    gh: GitHubAPI = context.application.bot_data["gh"]

    if not await is_admin(user.id, storage):
        return

    text = update.message.text or ""
    import re as _re

    m = _re.match(r"^/cancelbatch_([\w-]+)", text.strip())
    if not m:
        await update.message.reply_text("❌ Sai cú pháp.")
        return

    batch_prefix = m.group(1).replace("-", "")
    jobs = await storage.get_jobs()
    batch_jobs = [
        j for j in jobs if j.get("batch_id", "").replace("-", "")[:16] == batch_prefix[:16]
    ]

    if not batch_jobs:
        await update.message.reply_text(
            f"❌ Không tìm thấy batch `{batch_prefix[:8]}...`", parse_mode="Markdown"
        )
        return

    cancelled = 0
    batch_id = batch_jobs[0].get("batch_id") if batch_jobs else None
    for j in batch_jobs:
        status = j.get("status", "")
        run_id = j.get("run_id")
        if status in ("queued", "completed"):
            # Đánh dấu cancelled nếu chưa done thực sự
            if j.get("conclusion") not in ("success",):
                await storage.update_job(
                    j["_id"], {"status": "completed", "conclusion": "cancelled", "notified": True}
                )
                cancelled += 1
        elif status in ("dispatching", "dispatched", "in_progress", "running"):
            # Huỷ trên GitHub nếu có run_id
            if run_id:
                try:
                    await gh.cancel_run(j.get("repo", config.GKI_REPO), int(run_id))
                except Exception as e:
                    logger.warning("cancel_batch: cancel_run error for %s: %s", run_id, e)
            await storage.update_job(
                j["_id"], {"status": "completed", "conclusion": "cancelled", "notified": True}
            )
            cancelled += 1

    # Cập nhật batch message và xử lý card web
    if batch_id:
        # Kiểm tra xem có job nào thành công không
        updated_jobs = await storage.get_jobs_by_batch(batch_id)
        any_success = any(j.get("conclusion") == "success" for j in updated_jobs)

        if not any_success:
            # Không có gì thành công → xoá tin nhắn batch
            first_job = updated_jobs[0] if updated_jobs else None
            if first_job:
                chat_id = first_job.get("chat_id")
                msg_id = first_job.get("batch_msg_id")
                if chat_id and msg_id:
                    try:
                        await context.application.bot.delete_message(
                            chat_id=chat_id, message_id=msg_id
                        )
                    except Exception:
                        # Nếu xoá không được thì cập nhật text thay thế
                        try:
                            await context.application.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=msg_id,
                                text=f"❌ <b>Đã huỷ batch build.</b>\n🔨 {first_job.get('bs_variant', '')} — Đã huỷ {cancelled}/{len(batch_jobs)} bản.",
                                parse_mode="HTML",
                                disable_web_page_preview=True,
                            )
                        except Exception:
                            pass
        else:
            # Có ít nhất 1 bản thành công → cập nhật message
            await update_batch_message(batch_id, storage, context.application.bot)

    msg = await update.message.reply_text(
        f"✅ Đã huỷ <b>{cancelled}/{len(batch_jobs)}</b> jobs trong batch.", parse_mode="HTML"
    )
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=12, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(
                _del_msg_job,
                when=12,
                chat_id=update.message.chat_id,
                data=update.message.message_id,
            )


async def cmd_rebuild(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rebuild (các) job thất bại/hủy theo Custom ID."""
    await _safe_delete_user_msg(update, context)
    user = update.effective_user
    storage: HybridStorage = context.application.bot_data["storage"]
    if not await is_admin(user.id, storage):
        return

    text = update.message.text.strip()
    parts = text.split()
    if len(parts) < 2:
        return await _send_msg(
            update,
            context,
            "❌ Sai cú pháp. Dùng <code>/rb ID</code> (VD: <code>/rb NEXT0100.2227.280326</code>).",
            parse_mode="HTML",
        )
    custom_id = parts[1].split(":")[0].strip()

    jobs = await storage.get_jobs()
    target_batch_id = None
    for j in jobs:
        if j.get("type") == "buildsave" and j.get("batch_id"):
            cid = _make_custom_id(
                j.get("bs_variant", "SukiSU"), j.get("inputs", {}), j.get("created_at", "")
            )
            if cid == custom_id:
                target_batch_id = j["batch_id"]
                break

    if not target_batch_id:
        return await _send_msg(
            update,
            context,
            f"❌ Không tìm thấy lô build khớp với ID <b>{custom_id}</b>.",
            parse_mode="HTML",
        )

    batch_jobs = [
        j
        for j in jobs
        if j.get("batch_id") == target_batch_id
        and j.get("status") == "completed"
        and j.get("conclusion")
        in ("failure", "failed", "cancelled", "stale", "timed_out", "action_required")
    ]
    if not batch_jobs:
        return await _send_msg(
            update,
            context,
            "✅ Không có dòng nào bị lỗi trong lô thẻ này để build lại.",
            parse_mode="HTML",
        )

    total = len(batch_jobs)
    chat_id = update.effective_chat.id
    thread_id = (
        update.effective_message.message_thread_id
        if (update.effective_message and update.effective_message.is_topic_message)
        else None
    )
    msg = await _bot_send_topic_safe(
        context.bot,
        chat_id,
        f"🔄 <b>Đang Rebuild... {custom_id}</b>\n<b>⏳ Tiến trình: 0/{total}</b>\n<blockquote>Xem: <a href='https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions'><b>Github</b></a> | {_dashboard_link()}</blockquote>",
        message_thread_id=thread_id,
        parse_mode="HTML",
    )
    rebuild_msg_id = f"{msg.message_id}|{msg.chat_id}|{custom_id}"

    for j in batch_jobs:
        await storage.update_job(
            j["_id"], {"status": "rebuild_queued", "rebuild_msg_id": rebuild_msg_id}
        )


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

    if res["status"] in (202, 204, 404, 409):
        job = await storage.get_job_by_run_id(run_id)
        if job:
            await storage.update_job(
                job["_id"], {"status": "completed", "conclusion": "cancelled", "notified": True}
            )
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
            context.job_queue.run_once(
                _del_msg_job,
                when=10,
                chat_id=update.message.chat_id,
                data=update.message.message_id,
            )


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
            await msg.edit_text(
                f"✅ Run #{run_id} không tồn tại trên GitHub. Đã xoá khỏi dữ liệu nội bộ."
            )
        else:
            await msg.edit_text(f"❌ Lỗi xoá: {res['status']} {res.get('json', '')}")

    # Xoá tin nhắn sau 10s
    if context.job_queue:
        context.job_queue.run_once(_del_msg_job, when=10, chat_id=msg.chat_id, data=msg.message_id)
        if update.message:
            context.job_queue.run_once(
                _del_msg_job,
                when=10,
                chat_id=update.message.chat_id,
                data=update.message.message_id,
            )


async def cmd_data_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restore a validated backup without extracting arbitrary archive paths."""
    import pathlib
    import shutil
    import tempfile

    storage = context.application.bot_data["storage"]
    if not await is_admin(update.effective_user.id, storage):
        return
    message = update.effective_message
    reply = message.reply_to_message
    document = reply.document if reply else None
    if document is None or not (document.file_name or "").lower().endswith(".zip"):
        await message.reply_text("❌ Reply vào file .zip backup rồi gõ /data.")
        return

    status = await message.reply_text("⏳ Đang kiểm tra và khôi phục dữ liệu...")
    try:
        restored = []
        web_root = pathlib.Path(__file__).resolve().parent.parent / "web" / "data"
        with tempfile.TemporaryDirectory(prefix="kernel-restore-") as directory:
            archive_path = pathlib.Path(directory) / "backup.zip"
            telegram_file = await context.bot.get_file(document.file_id)
            await telegram_file.download_to_drive(archive_path)
            snapshot, web_files = read_backup(archive_path, web_root)

            # Validate every input before replacing any file. Storage performs
            # the data replacement atomically under its normal mutation lock.
            if snapshot is not None:
                await storage.restore_data(snapshot)
                restored.append("data.json")
            for relative_path, content in web_files:
                destination = web_root / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    shutil.copy2(destination, str(destination) + ".bak")
                fd, temporary = tempfile.mkstemp(prefix=".restore-", dir=destination.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(content, stream, ensure_ascii=False, indent=2)
                    os.replace(temporary, destination)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            if web_files:
                restored.append("web/data/")
        await status.edit_text(
            "✅ Khôi phục thành công: " + ", ".join(restored) + ". File cũ được lưu với đuôi .bak."
        )
    except Exception as error:
        logger.exception("Backup restore failed")
        await status.edit_text(f"❌ Không thể khôi phục: {error}")


async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        msg = await update.message.reply_text("Sử dụng: /dl <version>\nVí dụ: /dl 5.10.149")
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, when=10, chat_id=chat_id, data=msg.message_id)
        return

    version = args[0].strip()
    storage: HybridStorage = context.application.bot_data["storage"]

    all_jobs = await storage.get_jobs()
    variants_map = {}

    # Website là nguồn chính cho catalog tải xuống.
    for variant, download_url in _load_web_kernel_downloads(version).items():
        variants_map[variant] = {"download_url": download_url}

    # Lịch sử bot là nguồn bổ sung, đồng thời cung cấp metadata cấu hình nếu còn giữ job.
    for job in sorted(all_jobs, key=lambda x: x.get("created_at", "")):
        if (
            job.get("type") == "buildsave"
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
        ):
            if job.get("bs_full_ver") == version:
                variant = job.get("bs_variant")
                if variant:
                    variants_map.setdefault(variant, {}).update({"job": job})

    if not variants_map:
        msg = await update.message.reply_text(
            f"Chưa có link tải nào cho version {version} trên website hoặc lịch sử bot"
        )
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, when=10, chat_id=chat_id, data=msg.message_id)
        return

    keyboard = []
    row = []
    for var in sorted(variants_map.keys()):
        row.append(InlineKeyboardButton(var, callback_data=f"dl_var:{version}:{var}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    await update.message.reply_text(
        f"📦 Chọn bản KernelSU cho {version}:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cb_dl_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    _, version, variant = data.split(":", 2)

    storage: HybridStorage = context.application.bot_data["storage"]
    gh: GitHubAPI = context.application.bot_data["gh"]

    await query.edit_message_text(f"⏳ Đang lấy thông tin {version} - {variant}...")

    all_jobs = await storage.get_jobs()
    target_job = None
    for job in sorted(all_jobs, key=lambda x: x.get("created_at", ""), reverse=True):
        if (
            job.get("type") == "buildsave"
            and job.get("status") == "completed"
            and job.get("conclusion") == "success"
        ):
            if job.get("bs_full_ver") == version and job.get("bs_variant") == variant:
                target_job = job
                break

    # Ưu tiên run_id trong lịch sử bot, fallback sang link nightly của website.
    download_url = _load_web_kernel_downloads(version).get(variant, "")
    run_id = target_job.get("run_id") if target_job else None
    if not run_id and download_url:
        import re

        match = re.search(r"/actions/runs/(\d+)", download_url)
        if match:
            run_id = int(match.group(1))

    if not run_id:
        await query.edit_message_text(
            "❌ Có kernel trên website nhưng link không chứa GitHub run ID hợp lệ."
        )
        return

    resp = await gh.list_artifacts_for_run(config.GKI_REPO, run_id)
    if resp["status"] != 200:
        # Vẫn trả link website đã lưu nếu API GitHub không còn artifact metadata.
        if download_url:
            text = (
                f"<b>{version} - {variant}</b>\n"
                f"⚠️ Không lấy được metadata artifact từ GitHub (có thể artifact đã hết hạn).\n"
                f"<blockquote><b><a href='{download_url}'>Mở link tải đã lưu trên website</a></b></blockquote>"
            )
            await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await query.edit_message_text(
                "❌ Lỗi lấy thông tin API hoặc bản build đã hết hạn (quá 90 ngày)."
            )
        return

    artifacts = resp.get("json", {}).get("artifacts", [])
    if not artifacts:
        await query.edit_message_text("❌ Không có file zip nào trong bản build này.")
        return

    selected_artifact = None
    selected_size = 0
    for a in artifacts:
        name = a.get("name", "")
        if (
            name.startswith(variant)
            and "Rejects" not in name
            and "Manager" not in name
            and "susfs-release" not in name
            and "AnyKernel" not in name
        ):
            selected_artifact = name
            selected_size = a.get("size_in_bytes", 0)
            break

    if not selected_artifact:
        selected_artifact = artifacts[0]["name"]
        selected_size = artifacts[0].get("size_in_bytes", 0)

    # Link tới trang run (tổng hợp tất cả artifacts)
    run_url = f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}"
    # Link tải trực tiếp file zip cụ thể
    dl_url = f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}/{selected_artifact}.zip"
    website_url = config.DASHBOARD_URL

    # Kích thước lấy từ GitHub API (đã có sẵn, không cần HEAD)
    if selected_size:
        mb = selected_size / (1024 * 1024)
        size_str = f"{mb:.1f} MB"
    else:
        size_str = "N/A"

    # Tính cấu hình từ job data (ưu tiên lấy từ inputs, fallback sang root)
    inputs = target_job.get("inputs", {}) if target_job else {}
    zram = inputs.get("use_zram", target_job.get("use_zram", True) if target_job else True)
    bbg = inputs.get("use_bbg", target_job.get("use_bbg", True) if target_job else True)
    susfs = susfs_enabled(inputs)
    kpm = kpm_enabled(inputs)

    cfg_parts = []
    if zram:
        cfg_parts.append("ZRAM")
    if bbg:
        cfg_parts.append("BBG")
    if susfs:
        cfg_parts.append("SUSFS")
    if kpm:
        cfg_parts.append("KPM")
    for feature, label in (
        ("use_nomount", "NoMount"),
        ("use_networking", "Networking"),
        ("droidspaces_ntsync", "NTSync"),
        ("cve_2026_43499_patch", "CVE-2026-43499"),
    ):
        if enabled(inputs.get(feature)):
            cfg_parts.append(label)
    cfg_str = " + ".join(cfg_parts) if cfg_parts else "Mặc định"

    filename = f"{selected_artifact}.zip"
    text = (
        f"<b><a href='{run_url}'>{filename}</a></b>\n"
        f"<b>Size:</b> {size_str}\n"
        f"<b>Cấu hình:</b> {cfg_str}\n"
        f"<blockquote><b>Link: <a href='{website_url}'>Website</a> | <a href='{dl_url}'>Download</a></b></blockquote>"
    )

    await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def start_web_server(app_bot):
    """Khởi chạy API Web Server cục bộ phục vụ Dashboard Realtime"""
    app = aiohttp_web.Application()

    async def health(request):
        supervisor = app_bot.bot_data.get("supervisor")
        data = supervisor.health() if supervisor else {"status": "starting", "workers": {}}
        storage = app_bot.bot_data["storage"]
        try:
            await asyncio.wait_for(storage.snapshot(), timeout=1.5)
            data["storage"] = "error" if storage.last_write_error else "ok"
            if storage.last_write_error:
                data["status"] = "degraded"
        except Exception:
            data.update(status="degraded", storage="error")
        data["last_backup"] = app_bot.bot_data.get("last_backup")
        data["last_github_success"] = getattr(app_bot.bot_data.get("gh"), "last_success", None)
        data["cloud_sync"] = storage.cloud_error or ("ok" if storage.mongo_uri else "disabled")
        return aiohttp_web.json_response(
            data,
            status=200 if data["status"] == "ok" else 503,
            headers={"Cache-Control": "no-store"},
        )

    app.router.add_get("/healthz", health)

    async def api_data(request):
        try:
            data = await get_realtime_data(app_bot)
            return aiohttp_web.json_response(data, headers={"Access-Control-Allow-Origin": "*"})
        except Exception as e:
            logger.error("Web API Error: %s", e)
            return aiohttp_web.json_response({"error": str(e)}, status=500)

    app.router.add_get("/api/data", api_data)

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Phục vụ static files cho source web mới (do webpack build ra)
    app.router.add_static("/dist", os.path.join(base_dir, "web", "dist"))
    app.router.add_static("/data", os.path.join(base_dir, "web", "data"))

    async def index(request):
        return aiohttp_web.FileResponse(os.path.join(base_dir, "web", "index.html"))

    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)

    runner = aiohttp_web.AppRunner(app)
    await runner.setup()

    site = aiohttp_web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    app_bot.bot_data["web_runner"] = runner
    logger.info("Dashboard listening on %s:%s", config.WEB_HOST, config.WEB_PORT)


async def _register_bot_commands(app):
    """Publish the command list shown by Telegram's / menu."""
    commands = [
        BotCommand("gki", "Build GKI Kernel"),
        BotCommand("oki", "Build OKI Kernel"),
        BotCommand("dl", "Lấy link tải Kernel"),
        BotCommand("ping", "Kiểm tra bot hoạt động"),
        BotCommand("help", "Hiện hướng dẫn lệnh"),
        BotCommand("jobs", "Xem hàng đợi và tiến trình bot"),
        BotCommand("canceljob", "Hủy job theo ID nội bộ"),
    ]
    try:
        await app.bot.set_my_commands(commands)
        logger.info("Registered %d Telegram bot commands", len(commands))
    except Exception as e:
        logger.warning("Failed to register Telegram bot commands: %s", e)


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

    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .build()
    )

    app.bot_data["storage"] = storage
    app.bot_data["gh"] = gh

    # Background tasks sẽ được chạy trong _post_init

    # Owner-only commands
    app.add_handler(CommandHandler("key", cmd_key, filters=filters.User(user_id=config.OWNER_ID)))
    app.add_handler(
        CommandHandler("keyvip", cmd_keyvip, filters=filters.User(user_id=config.OWNER_ID))
    )
    app.add_handler(CommandHandler("keys", cmd_keys, filters=filters.User(user_id=config.OWNER_ID)))

    # Admin commands (owner + static admins in .env)
    app.add_handler(CommandHandler("st", cmd_status))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("canceljob", cmd_cancel_local_job))
    app.add_handler(CommandHandler("resolvejob", cmd_resolve_job))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler(["bc", "broadcast"], cmd_broadcast))
    app.add_handler(
        CallbackQueryHandler(
            cb_broadcast_target,
            pattern=r"^broadcast:(dm|group|all)$",
            block=False,
        )
    )
    app.add_handler(CallbackQueryHandler(cb_list_page, pattern=r"^listpage:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_close_msg, pattern=r"^closemsg"))
    app.add_handler(CallbackQueryHandler(cb_refresh_st, pattern=r"^refresh_st"))
    app.add_handler(CallbackQueryHandler(cb_run_controls, pattern=r"^run:gki:\d+$"))
    app.add_handler(
        CallbackQueryHandler(
            cb_run_control_action, pattern=r"^runctl:(cancel|close):gki:\d+(?::\d+)?$", block=False
        )
    )
    app.add_handler(CallbackQueryHandler(cb_save_run, pattern=r"^saverun:\d+$"))
    app.add_handler(MessageHandler(filters.Regex(r"^/cancel_\d+(?:@[\w_]+)?$"), cmd_cancel_run))
    app.add_handler(
        MessageHandler(filters.Regex(r"^/cancelbatch_[\w-]+(?:@[\w_]+)?$"), cmd_cancel_batch)
    )
    app.add_handler(CommandHandler("rb", cmd_rebuild))
    app.add_handler(CommandHandler("rebuild", cmd_rebuild))
    app.add_handler(MessageHandler(filters.Regex(r"^/delete_\d+(?:@[\w_]+)?$"), cmd_delete_run))

    app.add_handler(CommandHandler("dl", cmd_dl))
    app.add_handler(CallbackQueryHandler(cb_dl_variant, pattern=r"^dl_var:"))
    app.add_handler(CommandHandler("data", cmd_data_restore))

    # DM/group tracker (group=99, catch-all, lowest priority)
    app.add_handler(MessageHandler(filters.ALL, dm_tracker), group=99)

    # GKI conversation
    gki_conv = build_gki_conversation(gh, storage, config)

    async def gki_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await ensure_user_started_bot(update, context):
            return ConversationHandler.END
        if not await check_user_job_limit(update, context):
            return ConversationHandler.END
        return await gki_conv.entry_points[0].callback(update, context)

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("gki", gki_entry)],
            states=gki_conv.states,
            fallbacks=gki_conv.fallbacks,
            per_user=True,
            per_chat=False,
            conversation_timeout=60,  # 60s timeout tránh conversation treo
        )
    )

    # OKI conversation
    oki_conv = build_oki_conversation(gh, storage, config)

    async def oki_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await ensure_user_started_bot(update, context):
            return ConversationHandler.END
        return await oki_conv.entry_points[0].callback(update, context)

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("oki", oki_entry)],
            states=oki_conv.states,
            fallbacks=oki_conv.fallbacks,
            per_user=True,
            per_chat=False,
            conversation_timeout=60,
        )
    )

    # /build — Build kernel lưu trữ (admin only)
    buildsave_conv = build_buildsave_conversation(gh, storage, config, update_batch_message)

    async def buildsave_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await ensure_user_started_bot(update, context):
            return ConversationHandler.END
        return await buildsave_conv.entry_points[0].callback(update, context)

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("build", buildsave_entry)],
            states=buildsave_conv.states,
            fallbacks=buildsave_conv.fallbacks,
            allow_reentry=True,
            name="buildsave_conversation",
            persistent=False,
            per_user=True,
            per_chat=False,
            conversation_timeout=60,
        )
    )

    async def _post_init(app_):
        await storage._sync_with_cloud()
        await storage.seed_dm_users_from_jobs()
        await storage.seed_groups_from_jobs()
        await _register_bot_commands(app_)
        app_.bot_data["send_broadcast"] = _send_broadcast_content
        supervisor = WorkerSupervisor(storage, config.OWNER_ID, config.WORKER_STALE_SECONDS)
        app_.bot_data["supervisor"] = supervisor
        for name, factory, critical in (
            ("dispatcher", dispatch_worker, True),
            ("poller", poller, True),
            ("notifications", notification_worker, True),
            ("broadcast", broadcast_worker, False),
            ("github-cache", status_worker, False),
            ("catalog", catalog_worker, False),
            ("backups", backup_worker, False),
            ("cloud-sync", cloud_worker, False),
            ("cleanup", cleanup_worker, False),
        ):
            supervisor.start(name, lambda factory=factory: factory(app_), critical=critical)
        supervisor.monitor_task = asyncio.create_task(supervisor.monitor(), name="worker-monitor")
        await start_web_server(app_)
        logger.warning("Bot initialized; dashboard at %s:%s", config.WEB_HOST, config.WEB_PORT)

    async def _stop_workers(app_):
        if app_.bot_data.get("workers_stopped"):
            return
        app_.bot_data["workers_stopped"] = True
        supervisor = app_.bot_data.get("supervisor")
        if supervisor:
            await supervisor.stop()
        workers = tuple(app_.bot_data.get("background_tasks", ()))
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        runner = app_.bot_data.pop("web_runner", None)
        if runner:
            await runner.cleanup()
        try:
            await asyncio.wait_for(storage.flush_cloud(), timeout=10)
        except Exception:
            logger.warning("Final cloud sync failed; durable local state is preserved")

    async def _post_shutdown(app_):
        await _stop_workers(app_)
        await gh.close()
        await storage.close()

    app.post_init = _post_init
    app.post_stop = _stop_workers
    app.post_shutdown = _post_shutdown

    logger.warning("Starting Telegram bot...")
    app.run_polling()


if __name__ == "__main__":
    main()
