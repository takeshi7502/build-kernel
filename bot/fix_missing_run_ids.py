import asyncio
import sys
import os
import motor.motor_asyncio
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import MONGODB_URI

RUN_ID_MAP = {
    "5.10.218": 23691181287,
    "5.10.226": 23691450343,
    "5.10.233": 23691643500,
    "5.10.236": 23691848957,
    "5.10.237": 23692015579,
    "5.10.240": 23692178863,
    "5.10.246": 23692340860
}

async def main():
    print("Connecting to MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    db = client["kernel_bot_db"]
    collection = db["storage_data"]
    
    doc = await collection.find_one({"_id": "master_data"})
    
    jobs = doc.setdefault("jobs", [])
    updated = 0
    
    for j in jobs:
        if j.get("type") == "buildsave" and j.get("bs_android") == "android12" and j.get("bs_kernel_ver") == "5.10" and j.get("bs_variant") == "Next":
            ver = j.get("bs_full_ver")
            if ver in RUN_ID_MAP:
                j["run_id"] = RUN_ID_MAP[ver]
                updated += 1
                
    if updated > 0:
        await collection.replace_one({"_id": "master_data"}, doc)
        print(f"Updated {updated} run_ids in MongoDB.")
        
        # update web JSON
        json_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web", "data", "android12", "5.10.json"))
        with open(json_path, "r", encoding="utf-8") as f:
            web_data = json.load(f)
        
        json_updates = 0
        for entry in web_data.get("entries", []):
            ker = entry.get("kernel")
            if ker in RUN_ID_MAP:
                if "downloads" not in entry:
                    entry["downloads"] = {}
                entry["downloads"]["Next"] = f"https://nightly.link/takeshi7502/GKI_KernelSU_SUSFS/actions/runs/{RUN_ID_MAP[ker]}"
                json_updates += 1
        
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(web_data, f, indent=4)
        print(f"Injected {json_updates} Next links into web/data/android12/5.10.json!")
        
        # Update VPS local cache just in case
        local_file = os.path.join(os.path.dirname(__file__), "data.json")
        if os.path.exists(local_file):
            import subprocess
            print("Purged VPS cached jobs by running fix_next... wait actually VPS doesn't have it.")
            
if __name__ == "__main__":
    asyncio.run(main())
