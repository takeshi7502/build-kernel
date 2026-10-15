"""One GitHub run cache shared by the poller and dashboard."""

import asyncio
import time

from . import config
from .workers import heartbeat


def cached_run(app, repo, run_id):
    fetched = app.bot_data.get("dashboard_repo_fetched", {}).get(repo, 0)
    if time.monotonic() - fetched > config.POLL_INTERVAL * 2:
        return None
    return app.bot_data.get("dashboard_runs", {}).get(repo, {}).get(str(run_id))


async def refresh_runs(app):
    cache = app.bot_data.setdefault("dashboard_runs", {})
    fetched = app.bot_data.setdefault("dashboard_repo_fetched", {})
    for repo in dict.fromkeys(value for value in (config.GKI_REPO, config.OKI_REPO) if value):
        heartbeat(app, "github-cache")
        result = await app.bot_data["gh"]._request(
            "GET",
            f"{app.bot_data['gh'].base}/repos/{config.GITHUB_OWNER}/{repo}/actions/runs?per_page=100",
        )
        if result.get("status") == 200:
            cache[repo] = {str(run["id"]): run for run in result["json"].get("workflow_runs", [])}
            fetched[repo] = time.monotonic()
            app.bot_data["github_last_sync"] = time.time()


async def status_worker(app):
    initial = True
    while True:
        heartbeat(app, "github-cache")
        jobs = await app.bot_data["storage"].get_jobs()
        active = any(
            job.get("status")
            in {"queued", "dispatching", "dispatched", "in_progress", "running", "rebuild_queued"}
            for job in jobs
        )
        viewed = time.monotonic() - app.bot_data.get("dashboard_last_request", 0) < 60
        if initial or active or viewed:
            await refresh_runs(app)
            initial = False
        await asyncio.sleep(config.POLL_INTERVAL)
