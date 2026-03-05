import config, aiohttp, asyncio, base64, sys
sys.stdout.reconfigure(encoding='utf-8')

async def main():
    headers = {"Authorization": f"token {config.GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    url = f"https://api.github.com/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/contents/.github/workflows/build.yml"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers) as r:
            j = await r.json()
            content = base64.b64decode(j["content"]).decode()
            lines = content.split("\n")
            # Print first 80 lines to see inputs and job structure
            for i, line in enumerate(lines[:80], start=1):
                print(f"{i}: {line}")

asyncio.run(main())
