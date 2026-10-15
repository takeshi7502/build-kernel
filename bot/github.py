"""GitHub Actions client and conservative dispatch/run reconciliation."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import aiohttp

logger = logging.getLogger(__name__)


def parse_utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def match_workflow_run(job, runs, jobs):
    """Match only unclaimed dispatches close to the original request time."""
    created = parse_utc(job["created_at"])
    claimed = {
        str(other["run_id"])
        for other in jobs
        if other.get("repo") == job.get("repo")
        and other.get("_id") != job.get("_id")
        and other.get("run_id")
    }
    candidates = []
    for run in runs:
        if run.get("event") != "workflow_dispatch" or str(run.get("id")) in claimed:
            continue
        path = str(run.get("path", "")).split("@", 1)[0]
        if path.rsplit("/", 1)[-1] != job.get("workflow_file"):
            continue
        if run.get("head_branch") != job.get("ref"):
            continue
        try:
            delta = (parse_utc(run["created_at"]) - created).total_seconds()
        except (KeyError, TypeError, ValueError):
            continue
        # GitHub timestamps have second precision; allow minimal clock skew.
        # Never attach a forgotten job to an unrelated build hours later.
        if -2 <= delta <= 20 * 60:
            candidates.append((abs(delta), int(run["id"]), run))
    return min(candidates, key=lambda item: item[:2])[2] if candidates else None


class GitHubAPI:
    def __init__(self, token, owner):
        self.token = token
        self.owner = owner
        self.base = "https://api.github.com"
        self.session = None

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
                connector=aiohttp.TCPConnector(limit=10),
            )
        return self.session

    async def close(self):
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _request(self, method, url, json_payload=None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kernel-build-bot",
        }
        # A timed-out POST may already have created a build. Retrying it can
        # charge the user once while starting multiple expensive builds.
        attempts = 3 if method in ("GET", "DELETE") else 1
        for attempt in range(attempts):
            try:
                session = await self._get_session()
                async with session.request(
                    method, url, headers=headers, json=json_payload
                ) as response:
                    if response.status == 204:
                        return {"status": 204, "json": None}
                    try:
                        data = await response.json()
                    except (ValueError, aiohttp.ContentTypeError):
                        data = {"message": await response.text()}
                    if response.status in (429, 502, 503, 504) and attempt + 1 < attempts:
                        try:
                            delay = min(
                                60, max(1, int(response.headers.get("Retry-After", 2**attempt)))
                            )
                        except ValueError:
                            delay = 2**attempt
                        await asyncio.sleep(delay)
                        continue
                    return {"status": response.status, "json": data}
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                if attempt + 1 == attempts:
                    logger.error("GitHub %s request failed: %s", method, type(error).__name__)
                    return {
                        "status": 503,
                        "json": {
                            "message": "GitHub connection failed",
                            "uncertain": method == "POST",
                        },
                    }
                await asyncio.sleep(2**attempt)
        return {"status": 503, "json": {"message": "GitHub is unavailable"}}

    def _repo_url(self, repo):
        return f"{self.base}/repos/{quote(self.owner, safe='')}/{quote(repo, safe='')}/actions"

    async def dispatch_workflow(self, repo, workflow_file, ref, inputs):
        cleaned = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in inputs.items()
            if value is not None
        }
        url = f"{self._repo_url(repo)}/workflows/{quote(workflow_file, safe='')}/dispatches"
        return await self._request("POST", url, {"ref": ref, "inputs": cleaned})

    async def get_run(self, repo, run_id):
        return await self._request("GET", f"{self._repo_url(repo)}/runs/{int(run_id)}")

    async def list_artifacts_for_run(self, repo, run_id):
        return await self._request(
            "GET", f"{self._repo_url(repo)}/runs/{int(run_id)}/artifacts?per_page=100"
        )

    async def list_runs_for_repo(self, repo, ref, created_iso, workflow_file=None):
        query = urlencode(
            {
                "branch": ref,
                "event": "workflow_dispatch",
                "per_page": 100,
                "created": f">={(parse_utc(created_iso) - timedelta(seconds=2)).isoformat()}",
            }
        )
        path = f"/workflows/{quote(workflow_file, safe='')}/runs" if workflow_file else "/runs"
        return await self._request("GET", f"{self._repo_url(repo)}{path}?{query}")

    async def cancel_run(self, repo, run_id):
        return await self._request("POST", f"{self._repo_url(repo)}/runs/{int(run_id)}/cancel")

    async def delete_run(self, repo, run_id):
        return await self._request("DELETE", f"{self._repo_url(repo)}/runs/{int(run_id)}")
