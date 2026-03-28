import asyncio
import sys
import os
import motor.motor_asyncio
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import MONGODB_URI

async def main():
    print("Deduplicating MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    db = client["kernel_bot_db"]
    collection = db["storage_data"]
    
    doc = await collection.find_one({"_id": "master_data"})
    jobs = doc.setdefault("jobs", [])
    
    unique_jobs = {}
    
    # We want to iterate jobs and keep the one with a run_id if duplicates exist
    for j in jobs:
        jid = j.get("_id")
        if not jid:
            continue
            
        if jid not in unique_jobs:
            unique_jobs[jid] = j
        else:
            # If the current one doesn't have a run_id, but the new one does, swap it.
            # If the existing one has a run_id, keep it.
            curr = unique_jobs[jid]
            if curr.get("run_id") is None and j.get("run_id") is not None:
                unique_jobs[jid] = j
                
    deduped = list(unique_jobs.values())
    removed = len(jobs) - len(deduped)
    
    if removed > 0:
        doc["jobs"] = deduped
        await collection.replace_one({"_id": "master_data"}, doc)
        print(f"Removed {removed} duplicate jobs from MongoDB.")
        
    # Also deduplicate VPS local cache
    local_file = os.path.join(os.path.dirname(__file__), "data.json")
    if os.path.exists(local_file):
        with open(local_file, "r", encoding="utf-8") as f:
            local_data = json.load(f)
            
        local_jobs = local_data.get("jobs", [])
        l_unique = {}
        for j in local_jobs:
            jid = j.get("_id")
            if not jid: continue
            if jid not in l_unique:
                l_unique[jid] = j
            else:
                curr = l_unique[jid]
                if curr.get("run_id") is None and j.get("run_id") is not None:
                    l_unique[jid] = j
                    
        l_deduped = list(l_unique.values())
        l_rem = len(local_jobs) - len(l_deduped)
        if l_rem > 0:
            local_data["jobs"] = l_deduped
            with open(local_file, "w", encoding="utf-8") as f:
                json.dump(local_data, f, indent=4)
            print(f"Removed {l_rem} duplicates from local data.json.")

if __name__ == "__main__":
    asyncio.run(main())
