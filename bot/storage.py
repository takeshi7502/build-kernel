import asyncio
import hashlib
import json
import logging
import os
import socket
import tempfile
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
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None


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

        self._new_store = not os.path.exists(self.path)
        # Do not materialize an empty database before a first cloud restore:
        # a transient connection error + restart must not turn it into a writer.

        if self.mongo_uri and AsyncIOMotorClient:
            try:
                self.client = AsyncIOMotorClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
                self.db = self.client["kernel_bot_db"]
                self.collection = self.db["storage_data"]
            except Exception as e:
                raise RuntimeError("Invalid MongoDB client configuration") from e

    def close(self):
        if self.client is not None:
            self.client.close()

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
        except Exception as e:
            logger.error("MongoDB push error: %s", e)

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
                    self._save_local(local_data)
                    logger.info("Restored MongoDB snapshot into local storage")
                self._new_store = False
                self._synced = True
                if self._can_push():
                    await self._push_cloud(local_data)
        except Exception as e:
            logger.error("MongoDB Sync Error: %s", e)
            if self._new_store:
                # A new installation may have a production backup waiting in
                # the cloud. Do not accept new writes until we can check it.
                raise RuntimeError(
                    "Cannot initialize new storage until MongoDB backup is reachable"
                ) from e

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
        for field in ("successful_builds", "dm_users", "group_chats"):
            if any(not isinstance(item, dict) for item in data.get(field, [])):
                raise ValueError(f"{field} must contain objects")

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
            return self._default_data()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._validate_data(data)
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
        except Exception as e:
            logger.error("Save JSON failed: %s", e)
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            raise

    async def _save(self, data: Dict[str, Any]):
        self._save_local(data)
        if self.collection is not None and self._can_push():
            await self._push_cloud(data)

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
            return [j for j in data.get("jobs", []) if not j.get("notified")]

    async def list_user_active_jobs(self, user_id: int) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load()
            return [
                j
                for j in data.get("jobs", [])
                if j.get("user_id") == user_id
                and j.get("status") in ["dispatched", "running", "in_progress", "queued"]
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
                if index < 30 or job.get("status") in active_statuses or not job.get("notified")
            ]

            # --- Nhóm Buildsave: Chỉ giữ lại các job còn hiệu lực (queued, dispatched) hoặc mới hơn cutoff ---
            # Các job queued/active luôn được giữ lại
            kept_buildsave = []
            deleted_bs = 0
            for j in buildsave_jobs:
                if j.get("status") in active_statuses or not j.get("notified"):
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
