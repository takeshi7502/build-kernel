import os
import json
import urllib.request
import re
import time

def load_env():
    env = {}
    try:
        with open('bot/.env', 'r') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k] = v.strip('"\'')
    except Exception:
        pass
    return env

env = load_env()
gh_token = env.get('GITHUB_TOKEN', '')

def gh_api(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    if gh_token:
        req.add_header('Authorization', f'Bearer {gh_token}')
    while True:
        try:
            with urllib.request.urlopen(req) as res:
                return json.loads(res.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                print(f"Rate limited by GitHub. Sleeping 60s...")
                time.sleep(60)
            else:
                raise e

def recover_for_android(android_ver_name, kernel_version, filepath):
    print(f"\n--- RECOVERING FOR {android_ver_name} {kernel_version} ---")
    if not os.path.exists(filepath):
        print(f"File {filepath} does not exist.")
        return
        
    print(f"Using Token: {'YES' if gh_token else 'NO (Expect rate limits after 60 reqs)'}")
    
    # 1. Fetch runs
    print("Fetching successful workflow_dispatch runs from GitHub API...")
    url = "https://api.github.com/repos/takeshi7502/GKI_KernelSU_SUSFS/actions/runs?status=success&event=workflow_dispatch&per_page=100"
    runs_data = gh_api(url)
    
    total_runs = runs_data.get('workflow_runs', [])
    print(f"Loaded {len(total_runs)} recent successful runs.")
    
    found_map = {}
    
    for i, r in enumerate(total_runs, 1):
        try:
            artifacts = gh_api(r['artifacts_url'])
            has_related = False
            for a in artifacts.get('artifacts', []):
                # match names like: MKSU_kernel-android12-5.10-246
                pattern = r'^(SukiSU|ReSukiSU|MKSU|Next)_kernel-' + android_ver_name + r'-' + kernel_version.replace('.', r'\.') + r'-(\d+)'
                m = re.match(pattern, a['name'])
                if m:
                    var = m.group(1)
                    ver = f"{kernel_version}.{m.group(2)}"
                    if ver not in found_map.get(var, []):
                        found_map.setdefault(ver, {})[var] = r['id']
                        print(f"  [{i}/{len(total_runs)}] Mapped: {ver} {var} -> Run {r['id']}")
                        has_related = True
        except Exception as e:
            print(f"Error fetching artifacts for run {r['id']}: {e}")
            
    # 2. Patch JSON
    data = json.load(open(filepath, encoding='utf-8'))
    patched = 0
    
    for e in data.get('entries', []):
        k = e['kernel']
        if k in found_map:
            dl = e.get('downloads', {})
            for var, rid in found_map[k].items():
                dl[var] = f"https://nightly.link/takeshi7502/GKI_KernelSU_SUSFS/actions/runs/{rid}"
            e['downloads'] = dl
            patched += 1

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    print(f"Successfully patched {patched} entries in {filepath}!")

if __name__ == '__main__':
    recover_for_android("android12", "5.10", "web/data/android12/5.10.json")
