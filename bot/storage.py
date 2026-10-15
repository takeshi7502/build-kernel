import asyncio
import copy
import hashlib
import json
import logging
import os
import socket
import tempfile
import time
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import fcntl
except ImportError:  # Windows development environments do not provide fcntl.
    fcntl = None

logger = logging.getLogger("storage")


def _parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


try:
    from pymongo import AsyncMongoClient
except ImportError:
    AsyncMongoClient = None


class _ProcessSafeAsyncLock:
    """Serialize bot/maintenance writes without blocking the event loop."""

    _locks = weakref.WeakValueDictionary()

    def __init__(self, lock_path: str):
        self._local_lock = None
        self._lock_path = lock_path
        self._handle = None

    async def __aenter__(self):
        key = (asyncio.get_running_loop(), self._lock_path)
        self._local_lock = self._locks.setdefault(key, asyncio.Lock())
        await self._local_lock.acquire()
        try:
            if fcntl is not None:
                self._handle = open(self._lock_path, "a+", encoding="utf-8")
                while True:
                    try:
                        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        await asyncio.sleep(0.05)
        except BaseException:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._local_lock.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._handle is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None
        finally:
            self._local_lock.release()


class HybridStorage:
    def __init__(
        self,
        path: str,
        mongo_uri: Optional[str] = None,
        sync_mode: str = "auto",
        writer_hostname: str = "",
    ):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        lock_id = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]
        self._lock = _ProcessSafeAsyncLock(
            os.path.join(tempfile.gettempdir(), f"gki-storage-{lock_id}.lock")
        )
        self.mongo_uri = mongo_uri
        self.sync_mode = (sync_mode or "auto").strip().lower()
        self.writer_hostname = (writer_hostname or "").strip().lower()
        self.hostname = socket.gethostname().strip().lower()
        self.client = None
        self.db = None
        self.collection = None
        self._cached_data = None
        self._cached_fingerprint = None
        self._cloud_revision = 0
        self._cloud_dirty = False
        self.last_cloud_success = None
        self.cloud_error = None
        self.last_write_error = None

        self._new_store = not os.path.exists(self.path)
        # Do not materialize an empty database before a first cloud restore:
        # a transient connection error + restart must not turn it into a writer.

        if self.mongo_uri and AsyncMongoClient is None:
            raise RuntimeError("MongoDB requires pymongo>=4.13; install requirements.txt")
        if self.mongo_uri:
            try:
                self.client = AsyncMongoClient(
                    self.mongo_uri,
                    serverSelectionTimeoutMS=5000,
                    connectTimeoutMS=5000,
                    socketTimeoutMS=10000,
                )
                self.db = self.client["kernel_bot_db"]
                self.collection = self.db["storage_data"]
            except Exception as e:
                raise RuntimeError("Invalid MongoDB client configuration") from e

    async def close(self):
        if self.client is not None:
            await self.client.close()

    def _resolved_sync_mode(self) -> str:
        mode = self.sync_mode
        if mode not in {"auto", "push", "pull", "off"}:
            mode = "auto"
        if mode == "auto":
            return "pull" if os.name == "nt" else "push"
        return mode

    def _can_push(self) -> bool:
        if not getattr(self, "_synced", False):
            return False
        if self._resolved_sync_mode() != "push":
            return False
        if self.writer_hostname and self.hostname != self.writer_hostname:
            return False
        return True

    def _can_pull(self) -> bool:
        return self._resolved_sync_mode() in {"push", "pull"}

    async def _push_cloud(self, data: Dict[str, Any]):
        if self.collection is None:
            return
        try:
            payload = {"_id": "master_data", **data}
            await self.collection.replace_one({"_id": "master_data"}, payload, upsert=True)
            self.last_cloud_success = time.time()
            self.cloud_error = None
            return True
        except Exception as e:
            self.cloud_error = type(e).__name__
            logger.error("MongoDB push error: %s", self.cloud_error)
            return False

    async def flush_cloud(self, force=False):
        """Coalesce changes and perform network I/O outside the storage lock."""
        if self.collection is None or not self._can_push() or not (force or self._cloud_dirty):
            return True
        async with self._lock:
            snapshot = self._load()
            revision = self._cloud_revision
        if not await self._push_cloud(snapshot):
            return False
        if revision == self._cloud_revision:
            self._cloud_dirty = False
        return True

    async def _sync_with_cloud(self):
        if self.collection is None or not self._can_pull():
            self._synced = True
            return
        try:
            cloud_doc = await self.collection.find_one({"_id": "master_data"})
            async with self._lock:
                local_data = self._load()
                # Do not resurrect deleted admins, keys or jobs by merging old
                # backup fields into an existing authoritative local database.
                fresh = self._new_store and local_data == self._default_data()
                if cloud_doc and (self._resolved_sync_mode() == "pull" or fresh):
                    local_data = {key: value for key, value in cloud_doc.items() if key != "_id"}
                    self._validate_data(local_data)
                    await self._run_io(self._save_local, local_data)
                    logger.info("Restored MongoDB snapshot into local storage")
                self._new_store = False
                self._synced = True
                self._cloud_dirty = self._can_push()
            await self.flush_cloud()
        except Exception as e:
            logger.error("MongoDB Sync Error: %s", e)
            if self._new_store:
                # A new installation may have a production backup waiting in
                # the cloud. Do not accept new writes until we can check it.
                raise RuntimeError(
                    "Cannot initialize new storage until MongoDB backup is reachable"
                ) from e
            # An existing local writer may continue while cloud is down, but
            # must still retry snapshots once connectivity returns.
            self._synced = True
            self.cloud_error = type(e).__name__
            self._cloud_dirty = self._can_push()

    @staticmethod
    def _default_data() -> Dict[str, Any]:
        return {
            "keys": {},
            "jobs": [],
            "messages": {},
            "admins": [],
            "auth_chats": [],
            "successful_builds": [],
            "dm_users": [],
            "group_chats": [],
        }

    @staticmethod
    def _validate_data(data):
        if not isinstance(data, dict):
            raise ValueError("root JSON value must be an object")
        for key, expected in (
            ("keys", dict),
            ("jobs", list),
            ("messages", dict),
            ("admins", list),
            ("auth_chats", list),
            ("successful_builds", list),
            ("dm_users", list),
            ("group_chats", list),
            ("outbox", list),
            ("broadcasts", dict),
            ("blocked_chats", list),
        ):
            if key in data and not isinstance(data[key], expected):
                raise ValueError(f"storage field {key} must be {expected.__name__}")
        if any(
            not isinstance(job, dict) or not isinstance(job.get("_id", 0), int)
            for job in data.get("jobs", [])
        ):
            raise ValueError("jobs must contain objects with integer IDs")
        if "last_job_id" in data and (
            type(data["last_job_id"]) is not int or data["last_job_id"] < 0
        ):
            raise ValueError("last_job_id must be a non-negative integer")
        for field in ("successful_builds", "dm_users", "group_chats", "outbox"):
            if any(not isinstance(item, dict) for item in data.get(field, [])):
                raise ValueError(f"{field} must contain objects")
        for record in data.get("outbox", []):
            if (
                not isinstance(record.get("id"), str)
                or type(record.get("chat_id")) is not int
                or record.get("kind") not in {"completion", "notice", "broadcast"}
                or record.get("status") not in {"pending", "sent", "failed", "superseded"}
                or type(record.get("attempts")) is not int
                or record["attempts"] < 0
                or not isinstance(record.get("job_ids"), list)
                or not isinstance(record.get("next_attempt", 0), (int, float))
                or not isinstance(record.get("created_at", 0), (int, float))
            ):
                raise ValueError("outbox contains an invalid delivery record")
        if any(
            not isinstance(batch, dict) or not isinstance(batch.get("payload"), dict)
            for batch in data.get("broadcasts", {}).values()
        ):
            raise ValueError("broadcasts must contain objects with payloads")

    async def snapshot(self) -> Dict[str, Any]:
        """Return a consistent snapshot for backup without exposing the lock."""
        async with self._lock:
            return self._load()

    async def restore_data(self, data: Dict[str, Any]):
        """Validate and atomically restore a snapshot; retain a local backup."""
        self._validate_data(data)
        async with self._lock:
            previous = self._load()
            backup_path = self.path + ".bak"
            with open(backup_path, "w", encoding="utf-8") as backup:
                json.dump(previous, backup, ensure_ascii=False, indent=2)
            await self._save(dict(data))

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            if self._cached_data is not None:
                raise RuntimeError("Active storage file disappeared; refusing to reset state")
            return self._default_data()
        try:
            stat = os.stat(self.path)
            fingerprint = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if self._cached_data is not None and fingerprint == self._cached_fingerprint:
                return copy.deepcopy(self._cached_data)
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._validate_data(data)
            self._cached_data = copy.deepcopy(data)
            self._cached_fingerprint = fingerprint
            return data
        except (OSError, ValueError) as e:
            # Returning a blank database here would make the next write erase
            # every queued build and access key. Fail safely instead.
            logger.critical(
                "Cannot read storage file %s; refusing to overwrite it: %s", self.path, e
            )
            raise RuntimeError(f"Storage data is unreadable: {self.path}") from e

    def _save_local(self, data: Dict[str, Any]):
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix=".storage-", suffix=".json", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
            self._new_store = False
            stat = os.stat(self.path)
            self._cached_fingerprint = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            self._cached_data = copy.deepcopy(data)
            self.last_write_error = None
        except Exception as e:
            self.last_write_error = type(e).__name__
            logger.error("Save JSON failed: %s", e)
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise

    @staticmethod
    async def _run_io(function, *args):
        # A cancelled worker must wait for its disk write before releasing the
        # mutation lock, otherwise another writer can overwrite the wrong state.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            task.result()
            raise

    async def _save(self, data: Dict[str, Any]):
        try:
            await self._run_io(self._save_local, data)
        finally:
            # Cancellation can arrive after the thread committed the file.
            # Keep the cloud snapshot dirty even in that case.
            self._cloud_revision += 1
            self._cloud_dirty = True

    # ==========================
    # KEYS
    # ==========================
    async def set_key(self, code: str, uses: int, vip: bool = False):
        async with self._lock:
            data = self._load()
            data.setdefault("keys", {})[code] = {"uses": uses, "vip": vip}
            await self._save(data)

    async def get_uses(self, code: str) -> int:
        async with self._lock:
            data = self._load()
            info = data.get("keys", {}).get(code, {})
            return int(info.get("uses", 0) if isinstance(info, dict) else info)

    async def is_vip_key(self, code: str) -> bool:
        async with self._lock:
            data = self._load()
            info = data.get("keys", {}).get(code, {})
            if isinstance(info, dict):
                return bool(info.get("vip", False))
            return False

    async def get_all_keys(self) -> Dict[str, Any]:
        async with self._lock:
            data = self._load()
            keys = data.get("keys", {})
            result = {}
            for code, info in keys.items():
                if isinstance(info, dict):
                    result[code] = {
                        "uses": int(info.get("uses", 0)),
                        "vip": bool(info.get("vip", False)),
                    }
                else:
                    result[code] = {"uses": int(info), "vip": False}
            return result

    async def consume(self, code: str) -> bool:
        async with self._lock:
            data = self._load()
            if code not in data.get("keys", {}):
                return False
            info = data["keys"][code]
            uses = int(info.get("uses", 0)) if isinstance(info, dict) else int(info)
            if uses <= 0:
                return False
            if isinstance(info, dict):
                data["keys"][code]["uses"] = uses - 1
            else:
                data["keys"][code] = {"uses": uses - 1, "vip": False}
            await self._save(data)
            return True

    async def delete_key(self, code: str) -> bool:
        async with self._lock:
            data = self._load()
            if code in data.get("keys", {}):
                del data["keys"][code]
                await self._save(data)
                return True
            return False

    async def consume_job_key(self, job_id) -> bool:
        """Charge a reconciled uncertain dispatch once, atomically with its job."""
        async with self._lock:
            data = self._load()
            job = next((job for job in data.get("jobs", []) if job.get("_id") == job_id), None)
            if job is None:
                return False
            code = job.get("pending_key")
            if not code:
                return True
            info = data.get("keys", {}).get(code, {})
            uses = int(info.get("uses", 0) if isinstance(info, dict) else info)
            if uses <= 0:
                return False
            data["keys"][code] = (
                {**info, "uses": uses - 1}
                if isinstance(info, dict)
                else {"uses": uses - 1, "vip": False}
            )
            job.pop("pending_key", None)
            await self._save(data)
            return True

    # ==========================
    # JOBS
    # ==========================
    @staticmethod
    def _insert_job(data, job):
        jobs = data.setdefault("jobs", [])
        job_id = (
            max(data.get("last_job_id", 0), max((j.get("_id", 0) for j in jobs), default=0)) + 1
        )
        data["last_job_id"] = job_id
        job = copy.deepcopy(job)
        job["_id"] = job_id
        job.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        jobs.append(job)
        return job

    async def enqueue_job(self, job, key=None, request_id=None):
        """Persist intent and reserve a key in one write before any remote call."""
        async with self._lock:
            data = self._load()
            request_id = request_id or str(uuid.uuid4())
            existing = next(
                (j for j in data.get("jobs", []) if j.get("request_id") == request_id), None
            )
            if existing:
                return existing
            if (
                len(
                    [
                        j
                        for j in data.get("jobs", [])
                        if j.get("status") in {"queued", "rebuild_queued"}
                    ]
                )
                >= 500
            ):
                raise ValueError("Hàng đợi đang đầy. Vui lòng thử lại sau.")
            job = dict(job, request_id=request_id, status="queued", notified=False, run_id=None)
            job["queued_at"] = datetime.now(timezone.utc).isoformat()
            if key:
                info = data.get("keys", {}).get(key, {})
                uses = int(info.get("uses", 0) if isinstance(info, dict) else info)
                if uses <= 0:
                    raise ValueError("Key không hợp lệ hoặc đã hết lượt.")
                vip = isinstance(info, dict) and info.get("vip", False)
                if not vip and any(
                    j.get("user_id") == job.get("user_id")
                    and j.get("status")
                    in {
                        "queued",
                        "dispatching",
                        "dispatched",
                        "running",
                        "in_progress",
                        "rebuild_queued",
                    }
                    for j in data.get("jobs", [])
                ):
                    raise ValueError("Bạn đang có một build chưa hoàn tất.")
                data["keys"][key] = (
                    {**info, "uses": uses - 1}
                    if isinstance(info, dict)
                    else {"uses": uses - 1, "vip": False}
                )
                job["key_reservation"] = {"code": key, "state": "reserved"}
            saved = self._insert_job(data, job)
            await self._save(data)
            return saved

    async def enqueue_batch(self, jobs, request_id):
        async with self._lock:
            data = self._load()
            existing = [j for j in data.get("jobs", []) if j.get("batch_request_id") == request_id]
            if existing:
                return existing
            queued = sum(
                j.get("status") in {"queued", "rebuild_queued"} for j in data.get("jobs", [])
            )
            if queued + len(jobs) > 500:
                raise ValueError("Hàng đợi đang đầy. Vui lòng thử lại sau.")
            saved = [self._insert_job(data, dict(job, batch_request_id=request_id)) for job in jobs]
            await self._save(data)
            return saved

    @staticmethod
    def _refund_reservation(data, job):
        hold = job.get("key_reservation", {})
        if hold.get("state") != "reserved":
            return
        code = hold.get("code")
        info = data.get("keys", {}).get(code)
        if info is not None:  # Never recreate a key revoked by an administrator.
            uses = int(info.get("uses", 0) if isinstance(info, dict) else info)
            data["keys"][code] = (
                {**info, "uses": uses + 1}
                if isinstance(info, dict)
                else {"uses": uses + 1, "vip": False}
            )
        hold["state"] = "refunded"

    async def claim_dispatch(self, max_active=10):
        async with self._lock:
            data = self._load()
            jobs = data.get("jobs", [])
            active = [
                j
                for j in jobs
                if j.get("status") in {"dispatching", "dispatched", "running", "in_progress"}
                or (j.get("cancel_requested") and j.get("run_id") and not j.get("github_completed"))
            ]
            if len(active) >= max_active:
                return None
            buildsave_busy = any(j.get("type") == "buildsave" for j in active)
            queued = sorted(
                (j for j in jobs if j.get("status") in {"queued", "rebuild_queued"}),
                key=lambda j: j.get("queued_at", j.get("created_at", "")),
            )
            for job in queued:
                if job.get("type") == "buildsave" and buildsave_busy:
                    continue
                if (
                    job.get("batch_id")
                    and job.get("status") != "rebuild_queued"
                    and any(
                        other.get("batch_id") == job["batch_id"]
                        and other.get("conclusion") == "cancelled"
                        for other in jobs
                    )
                ):
                    job.update(status="completed", conclusion="cancelled", notified=True)
                    await self._save(data)
                    continue
                job.update(
                    status="dispatching",
                    run_id=None,
                    conclusion=None,
                    notified=False,
                    dispatch_started_at=datetime.now(timezone.utc).isoformat(),
                )
                # Keep created_at stable: batch IDs shown in the UI depend on it.
                job["dispatch_generation"] = uuid.uuid4().hex
                job.pop("github_result", None)
                job.pop("github_completed", None)
                job.pop("catalog_pending", None)
                for field in (
                    "notification_enqueued",
                    "notification_generation",
                    "completion_text",
                    "dispatch_error",
                ):
                    job.pop(field, None)
                await self._save(data)
                return job
            return None

    async def finish_dispatch(self, job_id, *, accepted, run_id=None, uncertain=False, error=""):
        async with self._lock:
            data = self._load()
            job = next(j for j in data.get("jobs", []) if j.get("_id") == job_id)
            cancelled = job.get("conclusion") == "cancelled" or job.get("cancel_requested")
            hold = job.get("key_reservation", {})
            if accepted or uncertain:
                job.update(
                    status="dispatched", notified=False, run_id=run_id, dispatch_uncertain=uncertain
                )
                if accepted and hold.get("state") == "reserved":
                    hold["state"] = "consumed"
                if cancelled:
                    job["cancel_requested"] = True
            else:
                self._refund_reservation(data, job)
                job.update(
                    status="completed",
                    conclusion="failure",
                    notified=False,
                    dispatch_error=error,
                    completion_text="❌ Không thể gửi build tới GitHub. Lượt key đã được hoàn nếu có; hãy kiểm tra /st trước khi thử lại.",
                )
            await self._save(data)
            return job

    async def link_reconciled_run(self, job_id, run_id, generation=None):
        """Bind a fallback match without overwriting a newer direct dispatch result."""
        async with self._lock:
            data = self._load()
            jobs = data.get("jobs", [])
            job = next((j for j in jobs if j.get("_id") == job_id), None)
            if not job or job.get("dispatch_generation") != generation:
                return None
            if job.get("run_id"):
                return job["run_id"]
            if job.get("status") not in {"dispatching", "dispatched", "running", "in_progress"}:
                return None
            if any(
                j.get("repo") == job.get("repo") and str(j.get("run_id")) == str(run_id)
                for j in jobs
            ):
                return None
            job["run_id"] = int(run_id)
            await self._save(data)
            return job["run_id"]

    async def commit_reservation(self, job_id):
        async with self._lock:
            data = self._load()
            job = next(j for j in data.get("jobs", []) if j.get("_id") == job_id)
            hold = job.get("key_reservation", {})
            if hold.get("state") == "reserved":
                hold["state"] = "consumed"
                await self._save(data)

    async def cancel_local_job(self, job_id, user_id, admin=False):
        async with self._lock:
            data = self._load()
            job = next((j for j in data.get("jobs", []) if j.get("_id") == job_id), None)
            if not job or (not admin and job.get("user_id") != user_id):
                raise ValueError("Không tìm thấy job thuộc quyền quản lý của bạn.")
            if job.get("status") == "completed":
                raise ValueError("Job đã kết thúc; không thể hủy bằng lệnh này.")
            if job.get("status") in {"queued", "rebuild_queued"}:
                self._refund_reservation(data, job)
                job.update(status="completed", conclusion="cancelled", notified=True)
            else:
                job.update(cancel_requested=True, notified=False)
            await self._save(data)
            return job

    async def resolve_job(self, job_id, run_id=None, refund=False):
        """Operator-confirmed resolution of an ambiguous remote dispatch."""
        async with self._lock:
            data = self._load()
            job = next((j for j in data.get("jobs", []) if j.get("_id") == job_id), None)
            if not job:
                raise ValueError("Không tìm thấy job.")
            if job.get("run_id"):
                raise ValueError("Job đã có run ID; không thể ghi đè hoặc hoàn lượt ở đây.")
            if refund:
                if job.get("status") != "completed" or job.get("conclusion") != "unknown":
                    raise ValueError(
                        "Chỉ hoàn lượt sau khi job chưa rõ kết quả đã chuyển sang cần kiểm tra."
                    )
                self._refund_reservation(data, job)
                job.update(
                    status="completed",
                    conclusion="cancelled",
                    notified=True,
                    resolution="operator_confirmed_refund",
                )
            else:
                if any(
                    j.get("repo") == job.get("repo") and str(j.get("run_id")) == str(run_id)
                    for j in data.get("jobs", [])
                ):
                    raise ValueError("Run này đã được gán cho job khác.")
                job.update(
                    status="dispatched",
                    run_id=int(run_id),
                    notified=False,
                    dispatch_uncertain=False,
                    resolution="operator_linked_run",
                )
                if job.get("key_reservation", {}).get("state") == "reserved":
                    job["key_reservation"]["state"] = "consumed"
            for key in ("notification_enqueued", "notification_generation", "completion_text"):
                job.pop(key, None)
            for record in data.get("outbox", []):
                if job_id in record.get("job_ids", []) and record.get("status") == "pending":
                    record["status"] = "superseded"
            await self._save(data)
            return job

    async def add_job(self, job: Dict[str, Any]) -> Any:
        async with self._lock:
            data = self._load()
            jobs = data.setdefault("jobs", [])
            job_id = (
                max(data.get("last_job_id", 0), max((j.get("_id", 0) for j in jobs), default=0)) + 1
            )
            data["last_job_id"] = job_id
            job = dict(job)
            job["_id"] = job_id
            if "created_at" not in job:
                job["created_at"] = datetime.now(timezone.utc).isoformat()
            jobs.append(job)
            await self._save(data)
            return job_id

    async def update_job(self, job_id, fields: Dict[str, Any]):
        async with self._lock:
            data = self._load()
            for j in data.get("jobs", []):
                if j.get("_id") == job_id:
                    if (
                        fields.get("status") == "completed"
                        and fields.get("conclusion") == "cancelled"
                    ):
                        if j.get("status") in {"queued", "rebuild_queued"}:
                            self._refund_reservation(data, j)
                        elif j.get("status") in {
                            "dispatching",
                            "dispatched",
                            "running",
                            "in_progress",
                        }:
                            fields = dict(fields, cancel_requested=True, notified=False)
                    if fields.get("status") == "rebuild_queued":
                        for field in (
                            "notification_enqueued",
                            "completion_text",
                            "dispatch_started_at",
                            "cancel_requested",
                        ):
                            j.pop(field, None)
                        for record in data.get("outbox", []):
                            if (
                                job_id in record.get("job_ids", [])
                                and record.get("status") == "pending"
                            ):
                                record["status"] = "superseded"
                    j.update(fields)
                    break
            await self._save(data)

    async def get_jobs(self) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            return data.get("jobs", [])

    async def list_unnotified_jobs(self) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            return [
                j
                for j in data.get("jobs", [])
                if not j.get("notified") and not j.get("notification_enqueued")
            ]

    async def list_user_active_jobs(self, user_id: int) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            return [
                j
                for j in data.get("jobs", [])
                if j.get("user_id") == user_id
                and j.get("status")
                in [
                    "dispatching",
                    "dispatched",
                    "running",
                    "in_progress",
                    "queued",
                    "rebuild_queued",
                ]
            ]

    async def get_job_by_run_id(self, run_id: int) -> Optional[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            for j in data.get("jobs", []):
                if j.get("run_id") == run_id:
                    return j
            return None

    async def delete_job_by_run_id(self, run_id: int) -> bool:
        async with self._lock:
            data = self._load()
            jobs = data.get("jobs", [])
            new_jobs = [j for j in jobs if j.get("run_id") != run_id]
            if len(new_jobs) != len(jobs):
                data["jobs"] = new_jobs
                await self._save(data)
                return True
            return False

    async def delete_old_jobs(self, older_than_days: int = 30):
        async with self._lock:
            data = self._load()
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            jobs = data.get("jobs", [])

            # Tách biệt 2 nhóm job
            gki_oki_jobs = [j for j in jobs if j.get("type") in (None, "gki", "oki")]
            buildsave_jobs = [j for j in jobs if j.get("type") == "buildsave"]

            # Keep active jobs and pending notifications even when >30 builds exist.
            active_statuses = (
                "queued",
                "rebuild_queued",
                "dispatching",
                "dispatched",
                "running",
                "in_progress",
                "waiting",
                "pending",
            )
            gki_oki_jobs_sorted = sorted(
                gki_oki_jobs, key=lambda x: x.get("created_at", ""), reverse=True
            )
            kept_gki_oki = [
                job
                for index, job in enumerate(gki_oki_jobs_sorted)
                if index < 30
                or job.get("status") in active_statuses
                or not job.get("notified")
                or job.get("conclusion") == "unknown"
                or job.get("key_reservation", {}).get("state") == "reserved"
                or job.get("catalog_pending")
            ]

            # --- Nhóm Buildsave: Chỉ giữ lại các job còn hiệu lực (queued, dispatched) hoặc mới hơn cutoff ---
            # Các job queued/active luôn được giữ lại
            kept_buildsave = []
            deleted_bs = 0
            for j in buildsave_jobs:
                if (
                    j.get("status") in active_statuses
                    or not j.get("notified")
                    or j.get("conclusion") == "unknown"
                    or j.get("key_reservation", {}).get("state") == "reserved"
                    or j.get("catalog_pending")
                ):
                    kept_buildsave.append(j)  # Giữ lại nếu đang active
                    continue
                try:
                    ts_str = j.get("created_at", "")
                    if ts_str:
                        ts = _parse_timestamp(ts_str)
                        if ts >= cutoff:
                            kept_buildsave.append(j)
                        else:
                            deleted_bs += 1
                    else:
                        deleted_bs += 1
                except (TypeError, ValueError):
                    # Invalid legacy timestamps are not evidence that a job expired.
                    kept_buildsave.append(j)

            deleted = (len(gki_oki_jobs) - len(kept_gki_oki)) + deleted_bs
            other_jobs = [j for j in jobs if j.get("type") not in (None, "gki", "oki", "buildsave")]
            data["jobs"] = kept_gki_oki + kept_buildsave + other_jobs
            await self._save(data)
            return deleted

    async def add_successful_build(
        self, run_id: int, user_id: int, branch: str, user_name: str = ""
    ):
        async with self._lock:
            data = self._load()
            builds = data.setdefault("successful_builds", [])
            builds = [build for build in builds if str(build.get("run_id")) != str(run_id)]
            build = {
                "run_id": run_id,
                "user_id": user_id,
                "user_name": user_name,
                "branch": branch,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            builds.insert(0, build)
            data["successful_builds"] = builds[:50]
            await self._save(data)

    # ==========================
    # MESSAGES
    # ==========================
    async def queue_notifications(self, job_ids, destinations, text):
        """Atomically enqueue one delivery per destination, surviving restarts."""
        async with self._lock:
            data = self._load()
            outbox = data.setdefault("outbox", [])
            primary = job_ids[0]
            primary_job = next((j for j in data.get("jobs", []) if j.get("_id") == primary), {})
            generation = primary_job.get("notification_generation") or uuid.uuid4().hex
            for destination in destinations:
                record_id = f"job:{primary}:{generation}:{destination['chat_id']}"
                if not any(record.get("id") == record_id for record in outbox):
                    outbox.append(
                        dict(
                            destination,
                            id=record_id,
                            job_ids=job_ids,
                            text=text,
                            kind="completion",
                            generation=generation,
                            status="pending",
                            attempts=0,
                            created_at=time.time(),
                            next_attempt=0,
                        )
                    )
            for job in data.get("jobs", []):
                if job.get("_id") in job_ids:
                    job["notification_enqueued"] = True
                    job["notification_generation"] = generation
                    if not destinations:
                        job.update(notified=True, notification_status="no_recipient")
            await self._save(data)

    async def queue_notice(self, record_id, chat_id, text):
        async with self._lock:
            data = self._load()
            outbox = data.setdefault("outbox", [])
            if any(record.get("id") == record_id for record in outbox):
                return
            outbox.append(
                dict(
                    id=record_id,
                    chat_id=chat_id,
                    job_ids=[],
                    text=text,
                    kind="notice",
                    status="pending",
                    attempts=0,
                    created_at=time.time(),
                    next_attempt=0,
                )
            )
            await self._save(data)

    async def queue_broadcast(self, batch_id, payload, targets, progress_chat, progress_message):
        async with self._lock:
            data = self._load()
            batches = data.setdefault("broadcasts", {})
            if batch_id in batches:
                return
            batches[batch_id] = dict(
                payload=payload,
                progress_chat=progress_chat,
                progress_message=progress_message,
                total=len(targets),
                created_at=time.time(),
                progress_done=False,
            )
            blocked = set(data.get("blocked_chats", []))
            outbox = data.setdefault("outbox", [])
            for chat_id in dict.fromkeys(targets):
                outbox.append(
                    dict(
                        id=f"broadcast:{batch_id}:{chat_id}",
                        chat_id=chat_id,
                        job_ids=[],
                        broadcast_id=batch_id,
                        kind="broadcast",
                        status="failed" if chat_id in blocked else "pending",
                        attempts=0,
                        created_at=time.time(),
                        next_attempt=0,
                    )
                )
            await self._save(data)

    async def due_notifications(self, limit=20, kinds=None):
        async with self._lock:
            data = self._load()
            due = [
                record
                for record in data.get("outbox", [])
                if record.get("status") == "pending"
                and record.get("next_attempt", 0) <= time.time()
                and (kinds is None or record.get("kind") in kinds)
            ]
            due.sort(
                key=lambda record: (record.get("kind") == "broadcast", record.get("created_at", 0))
            )
            for record in due[:limit]:
                if record.get("broadcast_id"):
                    record["payload"] = (
                        data.get("broadcasts", {}).get(record["broadcast_id"], {}).get("payload")
                    )
            return due[:limit]

    async def notification_is_pending(self, record_id):
        async with self._lock:
            return any(
                record.get("id") == record_id and record.get("status") == "pending"
                for record in self._load().get("outbox", [])
            )

    async def finish_notification(
        self, record_id, *, success=False, permanent=False, delay=0, error=""
    ):
        async with self._lock:
            data = self._load()
            record = next((r for r in data.get("outbox", []) if r.get("id") == record_id), None)
            if record is None or record.get("status") != "pending":
                return
            record["attempts"] += 1
            record["error"] = error
            record["status"] = "sent" if success else "failed" if permanent else "pending"
            record["next_attempt"] = time.time() + delay
            for job in data.get("jobs", []):
                if job.get("_id") not in record.get("job_ids", []):
                    continue
                related = [
                    r
                    for r in data.get("outbox", [])
                    if job["_id"] in r.get("job_ids", [])
                    and r.get("generation") == job.get("notification_generation")
                ]
                if all(r.get("status") != "pending" for r in related):
                    sent = sum(r.get("status") == "sent" for r in related)
                    job["notified"] = True
                    job["notification_status"] = (
                        "delivered" if sent == len(related) else "partial" if sent else "failed"
                    )
            if error == "Forbidden":
                data["blocked_chats"] = sorted(
                    set(data.get("blocked_chats", [])) | {record["chat_id"]}
                )
            await self._save(data)

    async def broadcast_progress(self):
        async with self._lock:
            data = self._load()
            result = []
            for batch_id, batch in data.get("broadcasts", {}).items():
                if batch.get("progress_done"):
                    continue
                records = [r for r in data.get("outbox", []) if r.get("broadcast_id") == batch_id]
                sent = sum(r.get("status") == "sent" for r in records)
                failed = sum(r.get("status") == "failed" for r in records)
                result.append(
                    dict(
                        batch,
                        id=batch_id,
                        sent=sent,
                        failed=failed,
                        done=sent + failed == len(records),
                        total=len(records),
                    )
                )
            return result

    async def finish_broadcast_progress(self, batch_id):
        async with self._lock:
            data = self._load()
            if batch_id in data.get("broadcasts", {}):
                data["broadcasts"][batch_id]["progress_done"] = True
                await self._save(data)

    async def prune_outbox(self):
        async with self._lock:
            data = self._load()
            cutoff = time.time() - 7 * 86400
            data["outbox"] = [
                r
                for r in data.get("outbox", [])
                if r.get("status") == "pending" or r.get("created_at", 0) >= cutoff
            ]
            active_batches = {r.get("broadcast_id") for r in data["outbox"]}
            data["broadcasts"] = {
                key: value
                for key, value in data.get("broadcasts", {}).items()
                if key in active_batches
            }
            await self._save(data)

    async def track_message(self, message_id: int, chat_id: int, user_id: int):
        async with self._lock:
            data = self._load()
            data.setdefault("messages", {})[f"{chat_id}:{message_id}"] = {
                "message_id": message_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self._save(data)

    async def delete_old_messages(self, older_than_hours: int = 24):
        async with self._lock:
            data = self._load()
            cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
            messages = data.get("messages", {})
            deleted = 0
            for msg_id, info in list(messages.items()):
                try:
                    ts = _parse_timestamp(info["timestamp"])
                    if ts < cutoff:
                        del messages[msg_id]
                        deleted += 1
                except Exception:
                    del messages[msg_id]
                    deleted += 1
            data["messages"] = messages
            await self._save(data)
            return deleted

    # ==========================
    # ADMINS & GROUPS
    # ==========================
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
                await self._save(data)

    async def remove_admin(self, user_id: int) -> bool:
        async with self._lock:
            data = self._load()
            admins = data.get("admins", [])
            if user_id in admins:
                admins.remove(user_id)
                data["admins"] = admins
                await self._save(data)
                return True
            return False

    async def get_auth_chats(self) -> set:
        async with self._lock:
            data = self._load()
            return set(data.get("auth_chats", []))

    async def add_auth_chat(self, chat_id: int):
        async with self._lock:
            data = self._load()
            chats = set(data.get("auth_chats", []))
            chats.add(chat_id)
            data["auth_chats"] = list(chats)
            await self._save(data)

    async def remove_auth_chat(self, chat_id: int):
        async with self._lock:
            data = self._load()
            chats = set(data.get("auth_chats", []))
            chats.discard(chat_id)
            data["auth_chats"] = list(chats)
            await self._save(data)

    # ==========================
    # DM USERS (Broadcast)
    # ==========================
    async def track_dm_user(self, user_id: int, chat_id: int):
        """Lưu user đã từng DM bot và cập nhật chat_id nếu dữ liệu thay đổi."""
        async with self._lock:
            data = self._load()
            if chat_id in data.get("blocked_chats", []):
                data["blocked_chats"].remove(chat_id)
                await self._save(data)
            dm_users = data.setdefault("dm_users", [])
            existing = next((u for u in dm_users if u.get("user_id") == user_id), None)
            if existing:
                if existing.get("chat_id") != chat_id:
                    existing["chat_id"] = chat_id
                    await self._save(data)
            else:
                dm_users.append({"user_id": user_id, "chat_id": chat_id})
                await self._save(data)

    async def has_dm_user(self, user_id: int) -> bool:
        """Kiểm tra user đã từng start/DM bot để bot có thể gửi PM."""
        async with self._lock:
            data = self._load()
            return any(
                u.get("user_id") == user_id and u.get("chat_id", 0) > 0
                for u in data.get("dm_users", [])
            )

    async def get_dm_users(self) -> List[Dict[str, Any]]:
        """Trả về danh sách tất cả user đã từng DM bot."""
        async with self._lock:
            data = self._load()
            return data.get("dm_users", [])

    async def seed_dm_users_from_jobs(self):
        """Quét jobs cũ để truy vết user đã từng tương tác (chỉ private chat)."""
        async with self._lock:
            data = self._load()
            dm_users = data.setdefault("dm_users", [])
            # Xóa các entry group chat bị lưu nhầm (chat_id < 0)
            before = len(dm_users)
            dm_users[:] = [u for u in dm_users if u.get("chat_id", 0) > 0]
            existing_ids = {u.get("user_id") for u in dm_users}
            added = 0
            for job in data.get("jobs", []):
                uid = job.get("user_id")
                cid = job.get("chat_id")
                # Chỉ lấy private chat (chat_id > 0 = DM, < 0 = group)
                if uid and cid and cid > 0 and uid not in existing_ids:
                    dm_users.append({"user_id": uid, "chat_id": cid})
                    existing_ids.add(uid)
                    added += 1
            cleaned = before - (len(dm_users) - added)
            if added or cleaned:
                await self._save(data)
            return added

    # ==========================
    # GROUP CHATS (Broadcast)
    # ==========================
    async def track_group(self, chat_id: int, title: str = ""):
        """Lưu nhóm mà bot đang hoạt động (deduplicate theo chat_id)."""
        async with self._lock:
            data = self._load()
            if chat_id in data.get("blocked_chats", []):
                data["blocked_chats"].remove(chat_id)
                await self._save(data)
            groups = data.setdefault("group_chats", [])
            existing = next((g for g in groups if g.get("chat_id") == chat_id), None)
            if existing:
                if title and existing.get("title") != title:
                    existing["title"] = title
                    await self._save(data)
            else:
                groups.append({"chat_id": chat_id, "title": title})
                await self._save(data)

    async def get_group_chats(self) -> List[Dict[str, Any]]:
        """Trả về danh sách tất cả nhóm bot đã tham gia."""
        async with self._lock:
            data = self._load()
            return data.get("group_chats", [])

    async def seed_groups_from_jobs(self) -> int:
        """Quét jobs cũ để tìm group chat (chat_id < 0) và tự động thêm vào danh sách."""
        async with self._lock:
            data = self._load()
            groups = data.setdefault("group_chats", [])
            existing_ids = {g.get("chat_id") for g in groups}
            added = 0
            for job in data.get("jobs", []):
                cid = job.get("chat_id")
                if cid and cid < 0 and cid not in existing_ids:
                    groups.append({"chat_id": cid, "title": ""})
                    existing_ids.add(cid)
                    added += 1
            if added:
                await self._save(data)
            return added

    # ==========================
    # BUILDSAVE BATCH QUEUE
    # ==========================
    async def get_active_buildsave_count(self) -> int:
        async with self._lock:
            data = self._load()
            return sum(
                1
                for j in data.get("jobs", [])
                if j.get("type") == "buildsave"
                and j.get("status") in ["dispatched", "running", "in_progress"]
            )

    async def get_next_queued_buildsave(self) -> Optional[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            queued = [
                j
                for j in data.get("jobs", [])
                if j.get("type") == "buildsave" and j.get("status") == "queued"
            ]
            if not queued:
                return None
            # Sort by created_at ISO string string-wise comparison works
            queued.sort(key=lambda x: x.get("created_at", ""))
            return queued[0]

    async def get_jobs_by_batch(self, batch_id: str) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            jobs = [j for j in data.get("jobs", []) if j.get("batch_id") == batch_id]
            jobs.sort(key=lambda x: x.get("batch_index", 0))
            return jobs

    async def trigger_batch_update(self, batch_id: str):
        # Được gọi từ ngoài để đánh dấu message layout cần update
        # Cái này sẽ tự được gọi bằng helper funct ở main.py
        pass
