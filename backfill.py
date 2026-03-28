import asyncio
import json
from datetime import datetime, timezone
import aiohttp
from dotenv import load_dotenv
import os
import sys

# Thêm đường dẫn tới bot modules để import
sys.path.append(os.path.join(os.path.dirname(__file__), 'bot'))

from storage import HybridStorage
from config import GITHUB_TOKEN, GITHUB_OWNER, GKI_REPO

async def main():
    load_dotenv()
    storage = HybridStorage("data.json", mongo_uri=os.getenv("MONGODB_URI"), sync_mode="push")
    
    # 1. Kéo data từ cloud (MongoDB) mới nhất
    print("Pulling data from cloud DB...")
    data = None
    if storage.collection is not None:
        cloud_doc = await storage.collection.find_one({"_id": "master_data"})
        if cloud_doc:
            cloud_doc.pop("_id", None)
            data = cloud_doc
            
    if not data:
        print("No cloud data! Using local data.json.")
        data = storage._load()
        
    jobs = data.get("jobs", [])
    updated_count = 0
    
    async with aiohttp.ClientSession() as session:
        for job in jobs:
            # Kiểm tra cả job đơn lẻ
            if job.get("status") == "completed" and not job.get("gh_duration"):
                run_id = job.get("run_id")
                repo = job.get("repo") or GKI_REPO
                if run_id:
                    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
                    headers = {
                        "Accept": "application/vnd.github.v3+json",
                        "Authorization": f"token {GITHUB_TOKEN}"
                    }
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            rn = await resp.json()
                            t1_str = rn.get("run_started_at") or rn.get("created_at")
                            t2_str = rn.get("updated_at")
                            if t1_str and t2_str:
                                t1 = datetime.fromisoformat(t1_str.replace("Z", "+00:00"))
                                t2 = datetime.fromisoformat(t2_str.replace("Z", "+00:00"))
                                diff = int((t2 - t1).total_seconds())
                                if diff > 0:
                                    gh_dur = f"{diff//60}m {diff%60}s"
                                    job["gh_duration"] = gh_dur
                                    updated_count += 1
                                    print(f"Updated job {job.get('_id')} (type {job.get('type')}) run {run_id}: {gh_dur}")
                        else:
                            print(f"Failed to fetch {run_id}, status: {resp.status}")

            # Kiểm tra cả các sub-jobs (cho webhook/web dashboard)
            if job.get("type", "") == "buildsave" and "_batch_jobs" in job:
                for b_job in job["_batch_jobs"]:
                    if b_job.get("status") == "completed" and not b_job.get("gh_duration"):
                        run_id = b_job.get("run_id")
                        repo = b_job.get("repo") or GKI_REPO
                        if run_id:
                            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
                            headers = {
                                "Accept": "application/vnd.github.v3+json",
                                "Authorization": f"token {GITHUB_TOKEN}"
                            }
                            async with session.get(url, headers=headers) as resp:
                                if resp.status == 200:
                                    rn = await resp.json()
                                    t1_str = rn.get("run_started_at") or rn.get("created_at")
                                    t2_str = rn.get("updated_at")
                                    if t1_str and t2_str:
                                        t1 = datetime.fromisoformat(t1_str.replace("Z", "+00:00"))
                                        t2 = datetime.fromisoformat(t2_str.replace("Z", "+00:00"))
                                        diff = int((t2 - t1).total_seconds())
                                        if diff > 0:
                                            gh_dur = f"{diff//60}m {diff%60}s"
                                            b_job["gh_duration"] = gh_dur
                                            updated_count += 1
                                            print(f"Updated sub-job (parent {job.get('_id')}) run {run_id}: {gh_dur}")
                                else:
                                    print(f"Failed to fetch {run_id}, status: {resp.status}")
                        
    if updated_count > 0:
        print("Pushing updated data back to cloud...")
        data["jobs"] = jobs
        storage._save_local(data)
        await storage._push_cloud(data)
        print(f"Successfully backfilled and synced {updated_count} jobs to CLOUD.")
    else:
        print("No jobs needed updating on cloud.")

if __name__ == "__main__":
    asyncio.run(main())
