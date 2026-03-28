import asyncio
import os
import sys

# Setup path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from web_sync import get_realtime_data
import config
from storage import HybridStorage
from gki import GitHubAPI

async def main():
    class DummyBotData:
        def __init__(self):
            self.bot_data = {}
            
    storage = HybridStorage(
        os.path.join(os.path.dirname(__file__), "data.json"),
        config.MONGODB_URI,
        sync_mode=config.MONGODB_SYNC_MODE
    )
    gh = GitHubAPI(config.GITHUB_TOKEN, config.GITHUB_OWNER)
    
    app = DummyBotData()
    app.bot_data["storage"] = storage
    app.bot_data["gh"] = gh
    
    print("Fetching real-time data locally...")
    try:
        data = await get_realtime_data(app)
        builds = data.get("builds", [])
        print(f"Data fetched successfully! Status: {data.get('status')}, Found {len(builds)} builds.")
        
        buildsaves = [b for b in builds if b.get("type") == "buildsave"]
        print(f"Found {len(buildsaves)} buildsave jobs.")
    except Exception as e:
        import traceback
        print(f"ERROR OCCURRED IN get_realtime_data: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
