import config, aiohttp, asyncio, base64, sys, os
from io import StringIO
from ruamel.yaml import YAML

sys.stdout.reconfigure(encoding="utf-8")

OWNER = config.GITHUB_OWNER
REPO = config.GKI_REPO
TOKEN = config.GITHUB_TOKEN
BRANCH = config.GKI_DEFAULT_BRANCH
UPSTREAM_OWNER = config.UPSTREAM_OWNER

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# ─────────────────────────── GitHub API helpers ──────────────────────────────

async def sync_fork(session: aiohttp.ClientSession) -> bool:
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/merge-upstream"
    payload = {"branch": BRANCH}
    async with session.post(url, headers=HEADERS, json=payload) as r:
        body = await r.json()
        if r.status == 200:
            msg = body.get("message", "")
            if "already" in msg.lower() or "up to date" in msg.lower():
                print(f"ℹ️  Fork đã up-to-date với upstream ({UPSTREAM_OWNER}/{REPO}:{BRANCH})")
                return False
            else:
                print(f"✅ Đã sync fork từ upstream — {msg}")
                return True
        elif r.status == 409:
            print("⚠️  Fork có commit diverge với upstream.")
            print("    Đang force-reset fork branch về upstream...")
            await force_reset_to_upstream(session)
            return True
        else:
            print(f"❌ Sync thất bại: {r.status} – {body.get('message', body)}")
            sys.exit(1)
    return False

async def force_reset_to_upstream(session: aiohttp.ClientSession):
    url = f"https://api.github.com/repos/{UPSTREAM_OWNER}/{REPO}/git/refs/heads/{BRANCH}"
    async with session.get(url, headers=HEADERS) as r:
        data = await r.json()
        if r.status != 200:
            print(f"❌ Không lấy được SHA upstream: {r.status} – {data.get('message', data)}")
            sys.exit(1)
        upstream_sha = data["object"]["sha"]
        print(f"   Upstream SHA: {upstream_sha}")

    url = f"https://api.github.com/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}"
    payload = {"sha": upstream_sha, "force": True}
    async with session.patch(url, headers=HEADERS, json=payload) as r:
        data = await r.json()
        if r.status == 200:
            print(f"✅ Force-reset fork branch → {upstream_sha[:7]}")
        else:
            print(f"❌ Force-reset thất bại: {r.status} – {data.get('message', data)}")
            sys.exit(1)

async def get_file(session: aiohttp.ClientSession, path: str):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}"
    async with session.get(url, headers=HEADERS) as r:
        j = await r.json()
        if r.status != 200:
            raise Exception(f"{r.status} - {j.get('message', j)}")
        content = base64.b64decode(j["content"]).decode("utf-8")
        sha = j["sha"]
        return content, sha

async def put_file(session: aiohttp.ClientSession, path: str, content: str, sha: str, message: str):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode(),
        "sha": sha,
        "branch": BRANCH,
    }
    async with session.put(url, headers=HEADERS, json=payload) as r:
        if r.status in (200, 201):
            print(f"  ✅ {path}")
        else:
            j2 = await r.json()
            print(f"  ❌ {path}: {r.status} – {j2.get('message', '')}")

# ─────────────────────────── Patch logic ─────────────────────────────────────

def set_self_hosted(data):
    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        return
    for job_name, job_data in jobs.items():
        if isinstance(job_data, dict) and "runs-on" in job_data:
            job_data["runs-on"] = "self-hosted"

def patch_build_yml(content: str) -> str:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(content)
    
    inputs = data.get("on", {}).get("workflow_call", {}).get("inputs", {})
    if "sub_levels" not in inputs:
        inputs["sub_levels"] = {
            "description": "Comma-separated sub_levels to build (empty=all)",
            "required": False,
            "type": "string",
            "default": ""
        }
        
    if "build-kernel" in data.get("jobs", {}):
        steps = data["jobs"]["build-kernel"].get("steps", [])
        if steps and steps[0].get("id") != "check_sub":
            check_step = {
                "name": "Check sub_level filter",
                "id": "check_sub",
                "run": 'SUB_LEVELS="${{ inputs.sub_levels }}"\nCURRENT="${{ inputs.sub_level }}"\nif [ -z "$SUB_LEVELS" ]; then\n  echo "Building sub_level $CURRENT (no filter applied)"\n  echo "skip=false" >> $GITHUB_OUTPUT\nelif echo ",$SUB_LEVELS," | grep -q ",$CURRENT,"; then\n  echo "Building sub_level $CURRENT (matched filter)"\n  echo "skip=false" >> $GITHUB_OUTPUT\nelse\n  echo "Skipping sub_level $CURRENT (not in: $SUB_LEVELS)"\n  echo "skip=true" >> $GITHUB_OUTPUT\nfi\n'
            }
            steps.insert(0, check_step)
            for step in steps[1:]:
                if "if" in step:
                    old_if = step["if"]
                    if old_if.replace(" ", "") == "steps.check_sub.outputs.skip!='true'":
                        continue
                    if old_if.startswith("${{") and old_if.endswith("}}"):
                        old_if = old_if[3:-2].strip()
                    step["if"] = f"${{{{ steps.check_sub.outputs.skip != 'true' && ({old_if}) }}}}"
                else:
                    step["if"] = "${{ steps.check_sub.outputs.skip != 'true' }}"
                    
    set_self_hosted(data)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()

def patch_main_yml(content: str) -> str:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(content)
    
    inputs = data.get("on", {}).get("workflow_dispatch", {}).get("inputs", {})
    if "sub_levels" not in inputs:
        inputs["sub_levels"] = {
            "description": "指定 sub_level 列表 (逗号分隔, 留空=全部)",
            "type": "string",
            "default": "",
            "required": False
        }

    # Bỏ group giới hạn concurrency để chạy song song nhiều lệnh build
    if "concurrency" in data:
        del data["concurrency"]

    jobs = data.get("jobs", {})
    for job_name, job_data in jobs.items():
        if isinstance(job_data, dict) and job_name.startswith("build-a") and "uses" in job_data:
            w_block = job_data.get("with", {})
            if "sub_levels" not in w_block:
                w_block["sub_levels"] = "${{ inputs.sub_levels }}"
                
    set_self_hosted(data)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()

def patch_kernel_yml(content: str) -> str:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(content)
    
    for trigger in ["workflow_dispatch", "workflow_call"]:
        inputs = data.get("on", {}).get(trigger, {}).get("inputs", {})
        if "sub_levels" not in inputs:
            if trigger == "workflow_call":
                inputs["sub_levels"] = {
                    "description": "Comma-separated sub_levels to build (empty=all)",
                    "required": False,
                    "type": "string",
                    "default": ""
                }
            else:
                inputs["sub_levels"] = {
                    "description": "指定 sub_level 列表 (逗号分隔, 留空=全部)",
                    "type": "string",
                    "default": "",
                    "required": False
                }
                
    jobs = data.get("jobs", {})
    for job_name, job_data in jobs.items():
        if isinstance(job_data, dict) and job_name.startswith("build-kernels"):
            if "uses" in job_data and "build.yml" in job_data["uses"]:
                w_block = job_data.get("with", {})
                if "sub_levels" not in w_block:
                    w_block["sub_levels"] = "${{ inputs.sub_levels }}"
            
    set_self_hosted(data)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()

# ─────────────────────────── Main ────────────────────────────────────────────

async def main():
    print(f"🔄 Đang sync fork {OWNER}/{REPO} từ upstream {UPSTREAM_OWNER}/{REPO} (branch: {BRANCH})...")
    print()

    async with aiohttp.ClientSession() as s:
        await sync_fork(s)
        print()

        print("🔧 Re-applying workflow patches bằng yaml parser (ruamel.yaml) ...")

        files_to_patch = {
            ".github/workflows/build.yml": patch_build_yml,
            ".github/workflows/main.yml": patch_main_yml,
            ".github/workflows/kernel-a12-5-10.yml": patch_kernel_yml,
            ".github/workflows/kernel-a13-5-15.yml": patch_kernel_yml,
            ".github/workflows/kernel-a14-6-1.yml": patch_kernel_yml,
            ".github/workflows/kernel-a15-6-6.yml": patch_kernel_yml,
            ".github/workflows/kernel-a16-6-12.yml": patch_kernel_yml,
        }

        for path, patch_func in files_to_patch.items():
            print(f"  Đang vá {path}...")
            try:
                content, sha = await get_file(s, path)
            except Exception as e:
                print(f"  ⚠️ Lỗi tải {path}: {e}")
                continue
                
            new_content = patch_func(content)
            if new_content != content:
                print(f"  Đẩy {path} lên GitHub...")
                await put_file(s, path, new_content, sha, f"Đang auto-patch {path} với ruamel.yaml")
            else:
                print(f"  ℹ️ {path} đã có patch, bỏ qua.")

    print()
    print("✅ Xong! Fork đã được sync và tự động vá bằng ruamel.yaml.")

if __name__ == "__main__":
    asyncio.run(main())
