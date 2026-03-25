import os
import json
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("storage")

try:
    from motor.motor_asyncio import AsyncIOMotorClient
except ImportError:
    AsyncIOMotorClient = None

class HybridStorage:
    def __init__(self, path: str, mongo_uri: Optional[str] = None):
        self.path = path
        self._lock = asyncio.Lock()
        self.mongo_uri = mongo_uri
        self.db = None
        self.collection = None
        
        if not os.path.exists(self.path):
            self._save_local({"keys": {}, "jobs": [], "messages": {}, "admins": [], "auth_chats": [], "waiters": [], "successful_builds": []})
            
        if self.mongo_uri and AsyncIOMotorClient:
            try:
                client = AsyncIOMotorClient(self.mongo_uri, serverSelectionTimeoutMS=5000)
                self.db = client["kernel_bot_db"]
                self.collection = self.db["storage_data"]
            except Exception as e:
                logger.error("MongoDB connection failed: %s", e)

    async def _sync_with_cloud(self):
        try:
            cloud_doc = await self.collection.find_one({"_id": "master_data"})
            local_data = self._load()
            
            # If fresh local (no keys and no jobs) but cloud has data -> Restore
            if cloud_doc and not local_data.get("keys") and not local_data.get("jobs"):
                cloud_doc.pop("_id", None)
                async with self._lock:
                    self._save_local(cloud_doc)
                logger.warning("Restored local data.json from MongoDB Atlas!")
            elif self.collection:
                # Mirror local to cloud
                await self.collection.update_one(
                    {"_id": "master_data"}, 
                    {"$set": local_data}, 
                    upsert=True
                )
        except Exception as e:
            logger.error("MongoDB Sync Error: %s", e)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"keys": {}, "jobs": [], "messages": {}, "admins": [], "auth_chats": [], "waiters": [], "successful_builds": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"keys": {}, "jobs": [], "messages": {}, "admins": [], "auth_chats": [], "waiters": [], "successful_builds": []}

    def _save_local(self, data: Dict[str, Any]):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Save JSON failed: %s", e)

    async def _save(self, data: Dict[str, Any]):
        self._save_local(data)
        if self.collection:
            asyncio.create_task(
                self.collection.update_one({"_id": "master_data"}, {"$set": data}, upsert=True)
            )

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
            return int(data.get("keys", {}).get(code, {}).get("uses", 0))

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
                    result[code] = {"uses": int(info.get("uses", 0)), "vip": bool(info.get("vip", False))}
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

    # ==========================
    # JOBS
    # ==========================
    async def add_job(self, job: Dict[str, Any]) -> Any:
        async with self._lock:
            data = self._load()
            jobs = data.setdefault("jobs", [])
            job_id = max((j.get("_id", 0) for j in jobs), default=0) + 1
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
            return [j for j in data.get("jobs", []) if j.get("user_id") == user_id and j.get("status") in ["dispatched", "running", "in_progress", "queued"]]

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

    async def delete_old_jobs(self, older_than_days: int = 7):
        async with self._lock:
            data = self._load()
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            jobs = data.get("jobs", [])
            new_jobs = []
            deleted = 0
            for j in jobs:
                try:
                    ts_str = j.get("created_at", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            new_jobs.append(j)
                        else:
                            deleted += 1
                    else:
                        deleted += 1
                except Exception:
                    deleted += 1
            data["jobs"] = new_jobs
            await self._save(data)
            return deleted

    async def add_successful_build(self, run_id: int, user_id: int, branch: str, user_name: str = ""):
        async with self._lock:
            data = self._load()
            builds = data.setdefault("successful_builds", [])
            build = {"run_id": run_id, "user_id": user_id, "user_name": user_name, "branch": branch, "timestamp": datetime.now(timezone.utc).isoformat()}
            builds.insert(0, build)
            data["successful_builds"] = builds[:50]
            await self._save(data)

    # ==========================
    # MESSAGES
    # ==========================
    async def track_message(self, message_id: int, chat_id: int, user_id: int):
        async with self._lock:
            data = self._load()
            data.setdefault("messages", {})[str(message_id)] = {
                "chat_id": chat_id,
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
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
                    ts = datetime.fromisoformat(info["timestamp"])
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
    # WAITERS (Userbot)
    # ==========================
    async def add_waiter(self, user_id: int, chat_id: int, user_name: str = ""):
        async with self._lock:
            data = self._load()
            waiters = data.setdefault("waiters", [])
            if not any(w.get("user_id") == user_id for w in waiters):
                waiters.append({"user_id": user_id, "chat_id": chat_id, "user_name": user_name})
                await self._save(data)

    async def clear_waiters(self):
        async with self._lock:
            data = self._load()
            data["waiters"] = []
            await self._save(data)

    async def get_waiters(self) -> List[dict]:
        async with self._lock:
            data = self._load()
            return data.get("waiters", [])

    # ==========================
    # TELEGRAPH
    # ==========================
    def get_telegraph_token(self) -> Optional[str]:
        data = self._load()
        return data.get("telegraph_token")

    def set_telegraph_token(self, token: str):
        data = self._load()
        data["telegraph_token"] = token
        self._save_local(data)
        if self.collection:
            # Lửa chùa: Push on background without await since `set_telegraph_token` was synch in `main.py`
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.collection.update_one({"_id": "master_data"}, {"$set": data}, upsert=True))
            except RuntimeError:
                pass
