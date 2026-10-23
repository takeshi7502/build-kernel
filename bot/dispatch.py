"""Durable workflow dispatch. An ambiguous HTTP result is never resubmitted."""

import asyncio
import html
import logging

from . import config
from .gki_contract import upgrade_queued_job
from .workers import heartbeat

logger = logging.getLogger(__name__)


async def dispatch_once(app):
    storage = app.bot_data["storage"]
    github = app.bot_data["gh"]
    job = await storage.claim_dispatch(config.MAX_ACTIVE_BUILDS)
    if job is None:
        return False
    heartbeat(app, "dispatcher")
    inflight = app.bot_data.setdefault("dispatch_inflight", set())
    inflight.add(job["_id"])
    try:
        try:
            fields = upgrade_queued_job(job, next(iter(config.GKI_WORKFLOWS.values()), "main.yml"))
        except ValueError as error:
            await storage.finish_dispatch(job["_id"], accepted=False, error=str(error))
            await storage.update_job(
                job["_id"],
                {
                    "completion_text": f"❌ Cấu hình build cũ không còn hợp lệ: {html.escape(str(error))}\nLượt key đã được hoàn nếu có. Mở lại menu để chọn cấu hình mới."
                },
            )
            return True
        if fields:
            await storage.update_job(job["_id"], fields)
            job.update(fields)
        result = await github.dispatch_workflow(
            job["repo"],
            job["workflow_file"],
            job["ref"],
            job.get("dispatch_inputs", job.get("inputs", {})),
        )
        body = result.get("json") or {}
        body = body if isinstance(body, dict) else {}
        accepted = result.get("status") in {200, 201, 202, 204}
        uncertain = bool(body.get("uncertain")) or result.get("status", 0) >= 500
        run_id = body.get("workflow_run_id") if accepted else None
        job = await storage.finish_dispatch(
            job["_id"],
            accepted=accepted,
            run_id=run_id,
            uncertain=uncertain and not accepted,
            error=f"HTTP {result.get('status', 'unknown')}",
        )
    finally:
        inflight.discard(job["_id"])
    if accepted and job.get("user_id") != config.OWNER_ID:
        view = f"https://github.com/{config.GITHUB_OWNER}/{job['repo']}/actions"
        if run_id:
            view += f"/runs/{run_id}"
        await storage.queue_notice(
            f"admin-build:{job['_id']}:{job.get('dispatch_generation', '')}",
            config.OWNER_ID,
            f"🚀 Build {html.escape(job.get('type', 'gki').upper())} từ {html.escape(str(job.get('user_name', 'user')))} đã gửi GitHub.\n<a href='{html.escape(view, quote=True)}'>Xem build</a>",
        )
    return True


async def dispatch_worker(app):
    while True:
        heartbeat(app, "dispatcher")
        if not await dispatch_once(app):
            await asyncio.sleep(2)
