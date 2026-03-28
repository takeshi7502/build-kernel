import os
import sys
import json
import asyncio
import motor.motor_asyncio

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import MONGODB_URI

async def main():
    if not MONGODB_URI:
        print("Missing MONGODB_URI in config")
        return
        
    print("Connecting to MongoDB to purge fake jobs...")
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    db = client["kernel_bot_db"]
    collection = db["storage_data"]
    
    doc = await collection.find_one({"_id": "master_data"})
    if doc:
        jobs = doc.get("jobs", [])
        original_count = len(jobs)
        # Keep only non-fake jobs (fake jobs have _id >= 90000000)
        valid_jobs = [j for j in jobs if not (isinstance(j.get("_id"), int) and j.get("_id") >= 90000000)]
        doc["jobs"] = valid_jobs
        await collection.replace_one({"_id": "master_data"}, doc)
        print(f"MongoDB: Removed {original_count - len(valid_jobs)} fake jobs. {len(valid_jobs)} valid jobs remaining.")
    
    # Also purge the local bot/data.json cache on the VPS
    local_file = os.path.join(os.path.dirname(__file__), "data.json")
    if os.path.exists(local_file):
        with open(local_file, "r", encoding="utf-8") as f:
            try:
                local_data = json.load(f)
            except Exception:
                local_data = {"jobs": []}
                
        local_jobs = local_data.get("jobs", [])
        original_local_count = len(local_jobs)
        valid_local_jobs = [j for j in local_jobs if not (isinstance(j.get("_id"), int) and j.get("_id") >= 90000000)]
        local_data["jobs"] = valid_local_jobs
        
        with open(local_file, "w", encoding="utf-8") as f:
            json.dump(local_data, f, indent=4)
            
        print(f"VPS Local Cache: Removed {original_local_count - len(valid_local_jobs)} fake jobs.")
    else:
        print("VPS Local Cache not found, skipping.")
        
    print("\n✅ Purge complete! Please run 'pm2 restart all' to apply clean data.")

if __name__ == "__main__":
    asyncio.run(main())
