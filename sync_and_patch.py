"""
sync_and_patch.py
-----------------
Làm 2 việc trong 1 lần chạy:
  1. Sync fork từ upstream (GitHub API: merge-upstream)
  2. Re-apply tất cả workflow patches lên fork

Dùng khi upstream cập nhật và bạn muốn lấy code mới
mà không mất các patch của bot.

Cách chạy:
  python sync_and_patch.py
"""

import config, aiohttp, asyncio, base64, sys, os, re

sys.stdout.reconfigure(encoding="utf-8")

OWNER = config.GITHUB_OWNER
REPO = config.GKI_REPO
TOKEN = config.GITHUB_TOKEN
BRANCH = config.GKI_DEFAULT_BRANCH
UPSTREAM_OWNER = config.UPSTREAM_OWNER
PATCH_DIR = os.path.join(os.path.dirname(__file__), "workflows_patch")

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# ─────────────────────────── GitHub API helpers ──────────────────────────────

async def sync_fork(session: aiohttp.ClientSession) -> bool:
    """
    Merge upstream/{BRANCH} vào fork/{BRANCH} qua GitHub API.
    Trả về True nếu có thay đổi, False nếu đã up-to-date.
    """
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
            # Conflict: fork diverged – cần force update
            print("⚠️  Fork có commit diverge với upstream.")
            print("    Đang force-reset fork branch về upstream...")
            await force_reset_to_upstream(session)
            return True
        else:
            print(f"❌ Sync thất bại: {r.status} – {body.get('message', body)}")
            sys.exit(1)
    return False  # fallthrough safety


async def force_reset_to_upstream(session: aiohttp.ClientSession):
    """
    Khi fork diverge, lấy SHA mới nhất của upstream rồi force-update
    fork branch về đúng SHA đó.
    """
    # Lấy SHA đầu upstream
    url = f"https://api.github.com/repos/{UPSTREAM_OWNER}/{REPO}/git/refs/heads/{BRANCH}"
    async with session.get(url, headers=HEADERS) as r:
        data = await r.json()
        if r.status != 200:
            print(f"❌ Không lấy được SHA upstream: {r.status} – {data.get('message', data)}")
            sys.exit(1)
        upstream_sha = data["object"]["sha"]
        print(f"   Upstream SHA: {upstream_sha}")

    # Force-update fork branch
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
    """Lấy nội dung và SHA của file từ fork."""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}"
    async with session.get(url, headers=HEADERS) as r:
        j = await r.json()
        if r.status != 200:
            print(f"❌ Không lấy được {path}: {r.status} – {j.get('message', j)}")
            sys.exit(1)
        content = base64.b64decode(j["content"]).decode("utf-8")
        sha = j["sha"]
        return content, sha


async def put_file(session: aiohttp.ClientSession, path: str, content: str, sha: str, message: str):
    """Ghi file đã patch lên fork."""
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

async def get_sha_only(session: aiohttp.ClientSession, path: str):
    """Chỉ lấy SHA của file trên GitHub để có thể ghi đè."""
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}"
    async with session.get(url, headers=HEADERS) as r:
        if r.status == 200:
            j = await r.json()
            return j.get("sha")
        return None

# ─────────────────────────── Main ────────────────────────────────────────────

async def main():
    if not os.path.exists(PATCH_DIR):
        print(f"❌ Không tìm thấy thư mục patch: {PATCH_DIR}")
        sys.exit(1)

    print(f"🔄 Đang sync fork {OWNER}/{REPO} từ upstream {UPSTREAM_OWNER}/{REPO} (branch: {BRANCH})...")
    print()

    async with aiohttp.ClientSession() as s:
        # ── Bước 1: Sync fork ──
        await sync_fork(s)
        print()

        # ── Bước 2: Re-apply patches từ thư mục workflows_patch ──
        print("🔧 Re-applying workflow patches từ local workflows_patch/ ...")

        files_to_patch = [
            "build.yml",
            "main.yml",
            "kernel-a12-5-10.yml",
            "kernel-a13-5-15.yml",
            "kernel-a14-6-1.yml",
            "kernel-a15-6-6.yml",
        ]

        for fname in files_to_patch:
            local_path = os.path.join(PATCH_DIR, fname)
            if not os.path.exists(local_path):
                print(f"  ⚠️ Bỏ qua {fname}: không tìm thấy file local tại {local_path}")
                continue
                
            print(f"  Đẩy {fname} lên GitHub...")
            with open(local_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            gh_path = f".github/workflows/{fname}"
            sha = await get_sha_only(s, gh_path)
            
            await put_file(s, gh_path, content, sha,
                           f"patch: overwrite {fname} with customized local version [auto]")

    print()
    print("✅ Xong! Fork đã được sync và file workflow yêu thích của bạn đã ghi đè lên.")
    print()
    print("💡 Tip: Mỗi lần chạy script này bot sẽ luôn dùng file trong thư mục workflows_patch.")


if __name__ == "__main__":
    asyncio.run(main())
