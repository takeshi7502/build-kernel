import os, json
from dotenv import load_dotenv
import urllib.request

load_dotenv('.env')
token = os.getenv("GITHUB_TOKEN")

req = urllib.request.Request(
    "https://api.github.com/gists",
    data=json.dumps({
        "description": "GKI Bot Web Data",
        "public": True,
        "files": {"web_data.json": {"content": '{"status": "offline", "last_ping": 0, "builds": []}'}}
    }).encode("utf-8"),
    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
)
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        gist_id = data["id"]
        print(f"GIST_ID={gist_id}")
        with open('.env', 'a', encoding='utf-8') as f:
            f.write(f"\n# KẾT NỐI WEB: ID của Github Gist dùng để đồng bộ dữ liệu\nGIST_ID={gist_id}\n")
        # Save to app.js as well
        with open('web/js/app.js', 'r', encoding='utf-8') as f:
            js = f.read()
        js = f"const GIST_ID = '{gist_id}';\n" + js
        with open('web/js/app.js', 'w', encoding='utf-8') as f:
            f.write(js)
except Exception as e:
    print(f"Error: {e}")
