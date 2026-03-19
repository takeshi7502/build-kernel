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

def fix_build_yml(content: str) -> str:
    """
    Thêm sub_levels input và Check sub_level filter step vào build.yml.
    Nếu patch đã được apply (idempotent), không thay đổi gì.
    """
    # 1. Thêm sub_levels input (sau supp_op)
    if "sub_levels:" not in content.split("jobs:")[0]:
        old_supp = (
            "      supp_op:\n"
            "        required: false\n"
            "        type: boolean\n"
            "        default: false"
        )
        new_supp = (
            "      supp_op:\n"
            "        required: false\n"
            "        type: boolean\n"
            "        default: false\n"
            "      sub_levels:\n"
            '        description: "Comma-separated sub_levels to build (empty=all)"\n'
            "        required: false\n"
            "        type: string\n"
            '        default: ""'
        )
        content = content.replace(old_supp, new_supp, 1)

    # 2. Thêm check step là FIRST step trong steps:
    check_step = (
        "      - name: Check sub_level filter\n"
        "        id: check_sub\n"
        "        run: |\n"
        '          SUB_LEVELS="${{ inputs.sub_levels }}"\n'
        '          CURRENT="${{ inputs.sub_level }}"\n'
        '          if [ -z "$SUB_LEVELS" ]; then\n'
        '            echo "Building sub_level $CURRENT (no filter applied)"\n'
        '            echo "skip=false" >> $GITHUB_OUTPUT\n'
        '          elif echo ",$SUB_LEVELS," | grep -q ",$CURRENT,"; then\n'
        '            echo "Building sub_level $CURRENT (matched filter)"\n'
        '            echo "skip=false" >> $GITHUB_OUTPUT\n'
        "          else\n"
        '            echo "Skipping sub_level $CURRENT (not in: $SUB_LEVELS)"\n'
        '            echo "skip=true" >> $GITHUB_OUTPUT\n'
        "          fi\n"
        "\n"
    )

    if "Check sub_level filter" not in content:
        content = content.replace("    steps:\n", "    steps:\n" + check_step, 1)

    # 3. Thêm if condition vào mọi step SAU check step
    lines = content.split("\n")
    new_lines = []
    past_check = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if "id: check_sub" in line:
            past_check = True

        if past_check and line.strip().startswith("- name:") and "Check sub_level filter" not in line:
            indent = len(line) - len(line.lstrip())
            if_cond = " " * indent + "  if: steps.check_sub.outputs.skip != 'true'"

            new_lines.append(line)

            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if next_line.strip().startswith("if:"):
                    # Merge conditions nếu đã có if
                    existing = next_line.strip()[3:].strip()
                    for wrapper in ["${{", "}}"]:
                        existing = existing.replace(wrapper, "").strip()
                    merged = (
                        " " * indent
                        + f"  if: ${{{{ steps.check_sub.outputs.skip != 'true' && ({existing}) }}}}"
                    )
                    new_lines.append(merged)
                    i += 2
                    continue
                else:
                    new_lines.append(if_cond)
                    i += 1
                    continue

        new_lines.append(line)
        i += 1

    return "\n".join(new_lines)


def fix_kernel_yml(content: str) -> str:
    """
    Trong các kernel-*.yml:
    1. Xóa broken job-level if (contains matrix.sub_level)
    2. Thêm sub_levels input (workflow_dispatch + workflow_call)
    3. Pass sub_levels qua with: khi gọi build.yml
    """
    # 1. Xóa broken job-level if
    content = re.sub(
        r"    if: \$\{\{.*?contains.*?matrix\.sub_level.*?\}\}\n",
        "",
        content,
    )

    # 2. Thêm sub_levels vào workflow_dispatch inputs (nếu chưa có)
    if "sub_levels:" not in content.split("workflow_call:")[0]:
        # Thêm vào workflow_dispatch (sau cancel_susfs)
        dispatch_block = re.compile(
            r"(      cancel_susfs:\n"
            r"        description:.*?\n"
            r"        required:.*?\n"
            r"        type:.*?\n"
            r"        default:.*?\n)",
            re.DOTALL
        )
        new_dispatch = (
            r"\1"
            "      sub_levels:\n"
            '        description: "指定 sub_level 列表 (逗号分隔, 留空=全部)"\n'
            "        required: false\n"
            "        type: string\n"
            '        default: ""\n'
        )
        content = dispatch_block.sub(new_dispatch, content, count=1)

    if "sub_levels:" not in content.split("workflow_call:")[1].split("jobs:")[0]:
        # Thêm vào workflow_call inputs (sau called_from_main block, trước `jobs:`)
        call_end = re.compile(
            r"(      called_from_main:\n"
            r"        description:.*?\n"
            r"        required:.*?\n"
            r"        type:.*?\n"
            r"        default:.*?\n)\njobs:",
            re.DOTALL
        )
        new_call = (
            r"\1"
            "      sub_levels:\n"
            '        description: "指定 sub_level 列表 (逗号分隔, 留空=全部)"\n'
            "        required: false\n"
            "        type: string\n"
            '        default: ""\n\njobs:'
        )
        content = call_end.sub(new_call, content, count=1)

    # 3. Thêm sub_levels vào with: khi gọi build.yml
    if "sub_levels: ${{ inputs.sub_levels }}" not in content:
        for last_param in ["enable_susfs:", "supp_op:"]:
            pattern = re.compile(r"(      " + last_param + r" .+\n)")
            match = pattern.search(content)
            if match:
                old = match.group(0)
                new = old.rstrip("\n") + "\n      sub_levels: ${{ inputs.sub_levels }}\n"
                content = content.replace(old, new, 1)
                break

    return content


def fix_main_yml(content: str) -> str:
    """
    Trong main.yml:
    1. Thêm sub_levels vào workflow_dispatch inputs
    2. Pass sub_levels qua with: khi gọi kernel-*.yml
    """
    # 1. Thêm sub_levels vào workflow_dispatch inputs
    if "sub_levels:" not in content.split("jobs:")[0]:
        dispatch_block = re.compile(
            r"(      build_all:\n"
            r"        description:.*?\n"
            r"        type:.*?\n"
            r"        default:.*?\n)"
            r"(?!      sub_levels:)",
            re.DOTALL
        )
        new_dispatch = (
            r"\1"
            "      sub_levels:\n"
            '        description: "指定 sub_level 列表 (逗号分隔, 留空=全部)"\n'
            "        type: string\n"
            '        default: ""\n'
            "        required: false\n"
        )
        content = dispatch_block.sub(new_dispatch, content, count=1)

    # 2. Thêm sub_levels vào with: khi gọi kernel-*.yml
    lines = content.split("\n")
    new_lines = []
    
    for i, line in enumerate(lines):
        new_lines.append(line)
        
        # Nếu dòng hiện tại là called_from_main: true, check dòng tiếp theo có sub_levels chưa
        if line.strip() == "called_from_main: true":
            has_sub = False
            if i + 1 < len(lines):
                if "sub_levels:" in lines[i+1]:
                    has_sub = True
                    
            if not has_sub:
                indent = len(line) - len(line.lstrip())
                new_lines.append(" " * indent + "sub_levels: ${{ inputs.sub_levels }}")
                
    return "\n".join(new_lines)


# ─────────────────────────── Main ────────────────────────────────────────────

async def main():
    os.makedirs(PATCH_DIR, exist_ok=True)

    print(f"🔄 Đang sync fork {OWNER}/{REPO} từ upstream {UPSTREAM_OWNER}/{REPO} (branch: {BRANCH})...")
    print()

    async with aiohttp.ClientSession() as s:
        # ── Bước 1: Sync fork ──
        await sync_fork(s)
        print()

        # ── Bước 2: Re-apply patches ──
        print("🔧 Re-applying workflow patches...")

        # build.yml
        print("  Patching build.yml...")
        content, sha = await get_file(s, ".github/workflows/build.yml")
        fixed = fix_build_yml(content)
        with open(os.path.join(PATCH_DIR, "build.yml"), "w", encoding="utf-8") as f:
            f.write(fixed)
        await put_file(s, ".github/workflows/build.yml", fixed, sha,
                       "patch: re-apply sub_levels filter in build.yml [auto]")
                       
        # main.yml
        print("  Patching main.yml...")
        content, sha = await get_file(s, ".github/workflows/main.yml")
        fixed = fix_main_yml(content)
        with open(os.path.join(PATCH_DIR, "main.yml"), "w", encoding="utf-8") as f:
            f.write(fixed)
        await put_file(s, ".github/workflows/main.yml", fixed, sha,
                       "patch: add sub_levels pass-through in main.yml [auto]")

        # kernel-*.yml
        for fname in [
            "kernel-a12-5-10.yml",
            "kernel-a13-5-15.yml",
            "kernel-a14-6-1.yml",
            "kernel-a15-6-6.yml",
        ]:
            print(f"  Patching {fname}...")
            path = f".github/workflows/{fname}"
            content, sha = await get_file(s, path)
            fixed = fix_kernel_yml(content)
            with open(os.path.join(PATCH_DIR, fname), "w", encoding="utf-8") as f:
                f.write(fixed)
            await put_file(s, path, fixed, sha,
                           f"patch: re-apply sub_levels pass-through in {fname} [auto]")

    print()
    print("✅ Xong! Fork đã được sync và patches đã được re-apply.")
    print()
    print("💡 Tip: Mỗi lần upstream update, chỉ cần chạy lại script này.")


asyncio.run(main())
