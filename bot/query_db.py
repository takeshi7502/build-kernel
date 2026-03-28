import asyncio
import sys
import os
import motor.motor_asyncio

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import MONGODB_URI

async def main():
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
    db = client["kernel_bot_db"]
    doc = await db["storage_data"].find_one({"_id": "master_data"})
    jobs = doc.get("jobs", [])
    
    # 1. How many Next jobs
    next_jobs = [j for j in jobs if j.get("bs_variant") == "Next" and j.get("bs_kernel_ver") == "5.10"]
    print(f"Total Next 5.10 jobs: {len(next_jobs)}")
    
    # 2. What are the .218 jobs?
    j218 = [j for j in next_jobs if j.get("bs_full_ver") == "5.10.218"]
    for j in j218:
        print(f"ID: {j.get('_id')}, RUN: {j.get('run_id')}, BATCH: {j.get('batch_id')}, STAT: {j.get('status')} {j.get('conclusion')} DUR: {j.get('gh_duration')}")
        
    # 3. Print the two batches?
    batches = {}
    for j in next_jobs:
        batches[j.get("batch_id")] = batches.get(j.get("batch_id"), 0) + 1
    print("Batches for Next:", batches)

if __name__ == "__main__":
    asyncio.run(main())
