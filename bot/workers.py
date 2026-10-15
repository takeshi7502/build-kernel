"""Supervise long-running tasks and report health independently of PM2 uptime."""

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


def heartbeat(app, name):
    supervisor = app.bot_data.get("supervisor")
    if supervisor:
        supervisor.beat(name)


class WorkerSupervisor:
    def __init__(self, storage, owner_id, stale_seconds=600, restart_limit=3):
        self.storage = storage
        self.owner_id = owner_id
        self.stale_seconds = stale_seconds
        self.restart_limit = restart_limit
        self.workers = {}
        self.closing = False
        self.monitor_task = None

    def start(self, name, factory, critical=True):
        if name in self.workers:
            raise ValueError(f"Worker {name} already registered")
        state = dict(
            factory=factory,
            critical=critical,
            status="starting",
            heartbeat=time.monotonic(),
            restarts=deque(),
            error=None,
            task=None,
        )
        self.workers[name] = state
        self._spawn(name)

    def _spawn(self, name):
        state = self.workers[name]
        state.update(status="running", heartbeat=time.monotonic(), error=None)
        state["task"] = asyncio.create_task(self._run(name), name=name)

    async def _run(self, name):
        state = self.workers[name]
        try:
            await state["factory"]()
            if not self.closing:
                raise RuntimeError("Worker exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            state.update(status="failed", error=type(error).__name__)
            logger.exception("Worker %s failed", name)

    def beat(self, name):
        if name in self.workers:
            self.workers[name]["heartbeat"] = time.monotonic()

    async def check_once(self):
        now = time.monotonic()
        for name, state in self.workers.items():
            task = state["task"]
            stale = now - state["heartbeat"] > self.stale_seconds
            if not task.done() and not stale:
                continue
            state["status"] = "stale" if stale and not task.done() else "failed"
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            while state["restarts"] and now - state["restarts"][0] > 3600:
                state["restarts"].popleft()
            if len(state["restarts"]) >= self.restart_limit:
                if not state.get("exhausted_notified"):
                    await self.storage.queue_notice(
                        f"worker-exhausted:{name}:{int(now)}",
                        self.owner_id,
                        f"❌ Worker {name} đã hết lượt tự phục hồi. Kiểm tra /healthz và log, rồi restart bot sau khi xử lý nguyên nhân.",
                    )
                    state["exhausted_notified"] = True
                state["status"] = "exhausted"
                continue
            # Back off repeated failures; do not create a hot restart loop.
            if state["restarts"] and now - state["restarts"][-1] < min(
                60, 5 * 2 ** len(state["restarts"])
            ):
                continue
            state["restarts"].append(now)
            await self.storage.queue_notice(
                f"worker:{name}:{int(now)}",
                self.owner_id,
                f"⚠️ Worker {name} cần phục hồi. Hệ thống đang thử khởi động lại ({len(state['restarts'])}/{self.restart_limit}).",
            )
            self._spawn(name)

    async def monitor(self):
        while not self.closing:
            try:
                await self.check_once()
            except Exception:
                logger.exception("Worker health check failed")
            await asyncio.sleep(5)

    def health(self):
        now = time.monotonic()
        workers = {
            name: dict(
                status=state["status"],
                heartbeat_age_seconds=round(now - state["heartbeat"], 1),
                restarts=len(state["restarts"]),
            )
            for name, state in self.workers.items()
        }
        healthy = all(
            not state["critical"]
            or (
                state["status"] == "running"
                and not state["task"].done()
                and now - state["heartbeat"] <= self.stale_seconds
            )
            for state in self.workers.values()
        )
        healthy = healthy and (self.monitor_task is None or not self.monitor_task.done())
        return dict(status="ok" if healthy and self.workers else "degraded", workers=workers)

    async def stop(self):
        self.closing = True
        tasks = [state["task"] for state in self.workers.values()]
        if self.monitor_task:
            tasks.append(self.monitor_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
