import asyncio
import json
from datetime import datetime, timezone
import aiohttp
from dotenv import load_dotenv
import os

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_OWNER = os.getenv("GITHUB_OWNER")

async def main():
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("data.json not found")
        return
        
    jobs = data.get("jobs", [])
    updated_count = 0
    
    async with aiohttp.ClientSession() as session:
        for job in jobs:
            if job.get("status") == "completed" and "gh_duration" not in job:
                run_id = job.get("run_id")
                repo = job.get("repo") or os.getenv("GKI_REPO")
                if not run_id or not repo:
                    continue
                    
                url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}/actions/runs/{run_id}"
                headers = {
                    "Accept": "application/vnd.github.v3+json",
                    "Authorization": f"token {GITHUB_TOKEN}"
                }
                
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        rn = await resp.json()
                        t1_str = rn.get("run_started_at") or rn.get("created_at", "")
                        if t1_str:
                            t1 = datetime.fromisoformat(t1_str.replace("Z", "+00:00"))
                            t2_str = rn.get("updated_at")
                            if t2_str:
                                t2 = datetime.fromisoformat(t2_str.replace("Z", "+00:00"))
                                diff = int((t2 - t1).total_seconds())
                                if diff > 0:
                                    gh_dur = f"{diff//60}m {diff%60}s"
                                    job["gh_duration"] = gh_dur
                                    updated_count += 1
                                    print(f"Updated job {job['_id']} {repo} run {run_id}: {gh_dur}")
                    else:
                        print(f"Failed to fetch {run_id}, status: {resp.status}")
                        
    if updated_count > 0:
        with open("data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Successfully backfilled {updated_count} jobs.")
    else:
        print("No jobs needed updating.")

if __name__ == "__main__":
    asyncio.run(main())
