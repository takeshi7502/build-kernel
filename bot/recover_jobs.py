import os
import sys
import json
import asyncio
from datetime import datetime, timezone
import motor.motor_asyncio

# Setup path so we can import config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

async def main():
    if not config.MONGODB_URI:
        print("Missing MONGODB_URI in .env")
        return
        
    print("Connecting to MongoDB...")
    client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGODB_URI)
    db = client["kernel_bot_db"]
    collection = db["storage_data"]
    
    doc = await collection.find_one({"_id": "master_data"})
    if not doc:
        print("No master_data found in mongo. Creating new one.")
        doc = {"_id": "master_data", "jobs": [], "keys": {}}
    
    jobs = doc.setdefault("jobs", [])
    existing_run_ids = {j.get("run_id") for j in jobs if j.get("run_id")}
    
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web", "data"))
    records = []
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith(".json"):
                fpath = os.path.join(root, file)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    full_ver = os.path.basename(file).replace(".json", "")
                    for entry in data.get("entries", []):
                        for variant, link in entry.get("downloads", {}).items():
                            if "/runs/" not in link:
                                continue
                            run_id = link.split("/runs/")[-1].split("/")[0]
                            try:
                                run_id = int(run_id)
                            except ValueError:
                                continue
                                
                            if run_id in existing_run_ids:
                                continue
                                
                            # construct fake job
                            dt = entry.get("date", datetime.now(timezone.utc).isoformat())
                            if not dt.endswith("Z") and "+" not in dt:
                                dt += "Z" # Ensure valid ISO for standard parser
                                
                            job = {
                                "_id": 90000000 + run_id, # arbitrary high id
                                "type": "buildsave",
                                "status": "completed",
                                "conclusion": "success",
                                "run_id": run_id,
                                "created_at": dt,
                                "updated_at": dt,
                                "user_name": entry.get("runner", "Recovery"),
                                "zram": entry.get("zram", ""),
                                "kpm": entry.get("kpm", ""),
                                "bbg": entry.get("bbg", ""),
                                "susfs": entry.get("susfs", ""),
                                "bs_variant": variant,
                                "bs_full_ver": full_ver
                            }
                            records.append(job)
                            existing_run_ids.add(run_id)
                except Exception as e:
                    print(f"Error parse {fpath}: {e}")

    if not records:
        print("No missing buildsave jobs found to recover!")
        return
        
    jobs.extend(records)
    await collection.replace_one({"_id": "master_data"}, doc)
    print(f"✅ Successfully recovered {len(records)} buildsave jobs to MongoDB!")
    print("Please run: pm2 restart all")

if __name__ == "__main__":
    asyncio.run(main())
