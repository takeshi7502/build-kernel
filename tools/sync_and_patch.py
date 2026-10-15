"""Optional maintainer tool: sync and patch the configured GKI fork.

Nothing is sent to GitHub without --apply. A divergent branch is never reset.
"""

import argparse
import asyncio
import base64
import json
import sys
from io import StringIO
from pathlib import Path

import aiohttp
from ruamel.yaml import YAML

OWNER = REPO = TOKEN = BRANCH = UPSTREAM_OWNER = ""
HEADERS = {"Accept": "application/vnd.github+json"}

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
            raise RuntimeError(
                "Fork đã diverge với upstream. Hãy xử lý merge conflict thủ công; không tự force-reset."
            )
        else:
            print(f"❌ Sync thất bại: {r.status} – {body.get('message', body)}")
            raise RuntimeError("Không thể sync fork; dừng trước khi sửa workflows.")
    return False


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
            raise RuntimeError(f"{path}: HTTP {r.status} – {j2.get('message', '')}")


# ─────────────────────────── Patch logic ─────────────────────────────────────


def set_self_hosted(data):
    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        return
    for job_name, job_data in jobs.items():
        if isinstance(job_data, dict) and "runs-on" in job_data:
            if not str(job_name).startswith("setup-"):
                job_data["runs-on"] = "self-hosted"


def patch_build_yml(content: str) -> str:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(content)

    if data is None:
        return content

    inputs = data.setdefault("on", {}).setdefault("workflow_call", {}).setdefault("inputs", {})
    if "sub_levels" not in inputs:
        inputs["sub_levels"] = {
            "description": "Comma-separated sub_levels to build (empty=all)",
            "required": False,
            "type": "string",
            "default": "",
        }

    set_self_hosted(data)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def patch_main_yml(content: str) -> str:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(content)

    if data is None:
        return content

    inputs = data.setdefault("on", {}).setdefault("workflow_dispatch", {}).setdefault("inputs", {})
    if "sub_levels" not in inputs:
        inputs["sub_levels"] = {
            "description": "指定 sub_level 列表 (逗号分隔, 留空=全部)",
            "type": "string",
            "default": "",
            "required": False,
        }

    # Bỏ group giới hạn concurrency để chạy song song nhiều lệnh build
    if "concurrency" in data:
        del data["concurrency"]

    jobs = data.get("jobs", {})
    for job_name, job_data in jobs.items():
        if isinstance(job_data, dict) and job_name.startswith("build-a") and "uses" in job_data:
            w_block = job_data.setdefault("with", {})
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

    if data is None:
        return content

    for trigger in ["workflow_dispatch", "workflow_call"]:
        inputs = data.setdefault("on", {}).setdefault(trigger, {}).setdefault("inputs", {})
        if "sub_levels" not in inputs:
            if trigger == "workflow_call":
                inputs["sub_levels"] = {
                    "description": "Comma-separated sub_levels to build (empty=all)",
                    "required": False,
                    "type": "string",
                    "default": "",
                }
            else:
                inputs["sub_levels"] = {
                    "description": "指定 sub_level 列表 (逗号分隔, 留空=全部)",
                    "type": "string",
                    "default": "",
                    "required": False,
                }

    jobs = data.get("jobs", {})
    new_jobs = {}

    for job_name, job_data in jobs.items():
        if isinstance(job_data, dict) and job_name.startswith("build-kernels"):
            if "uses" in job_data and "build.yml" in job_data["uses"]:
                w_block = job_data.setdefault("with", {})
                if "sub_levels" not in w_block:
                    w_block["sub_levels"] = "${{ inputs.sub_levels }}"

                # Biến ma trận tĩnh thành ma trận JSON động thông qua Setup Job
                original_matrix = job_data.get("strategy", {}).get("matrix", {}).get("include", [])
                if original_matrix and not isinstance(original_matrix, str):
                    clean_matrix = [dict(x) for x in original_matrix]
                    matrix_json_str = json.dumps(clean_matrix, separators=(",", ":"))

                    setup_job_name = f"setup-{job_name}"
                    setup_job = {
                        "runs-on": "ubuntu-latest",
                        "outputs": {"matrix": "${{ steps.filter.outputs.matrix }}"},
                        "steps": [
                            {
                                "id": "filter",
                                "env": {
                                    "SUB_LEVELS": "${{ inputs.sub_levels }}",
                                    "MATRIX_JSON": matrix_json_str,
                                },
                                "run": """if [ -z "$SUB_LEVELS" ]; then
  JSON_OUT="$MATRIX_JSON"
else
  JSON_OUT=$(printf '%s' "$MATRIX_JSON" | jq --arg subs ",$SUB_LEVELS," -c 'map(select(.sub_level as $sl | $subs | contains("," + ($sl|tostring) + ",")))')
  if [ "$JSON_OUT" = "[]" ]; then
    echo "No sub_level matched the requested versions." >&2
    exit 1
  fi
fi
printf 'matrix=%s\\n' "$JSON_OUT" >> "$GITHUB_OUTPUT"
""",
                            }
                        ],
                    }
                    new_jobs[setup_job_name] = setup_job
                    needs = job_data.get("needs", [])
                    if isinstance(needs, str):
                        needs = [needs]
                    job_data["needs"] = list(dict.fromkeys([*needs, setup_job_name]))
                    job_data["strategy"]["matrix"]["include"] = (
                        f"${{{{ fromJSON(needs.{setup_job_name}.outputs.matrix) }}}}"
                    )

        new_jobs[job_name] = job_data

    data["jobs"] = new_jobs

    set_self_hosted(data)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()


# ─────────────────────────── Main ────────────────────────────────────────────


async def main(*, apply=False, skip_sync=False):
    global OWNER, REPO, TOKEN, BRANCH, UPSTREAM_OWNER, HEADERS
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot import config

    OWNER, REPO, TOKEN = config.GITHUB_OWNER, config.GKI_REPO, config.GITHUB_TOKEN
    BRANCH, UPSTREAM_OWNER = config.GKI_DEFAULT_BRANCH, config.UPSTREAM_OWNER
    HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}
    print(
        f"🔄 Đang sync fork {OWNER}/{REPO} từ upstream {UPSTREAM_OWNER}/{REPO} (branch: {BRANCH})..."
    )
    print()

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        if apply and not skip_sync:
            await sync_fork(s)
        elif not apply:
            print("Chế độ xem trước: không sync fork, không ghi GitHub. Thêm --apply để thực hiện.")
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

        failures = []
        for path, patch_func in files_to_patch.items():
            print(f"  Đang vá {path}...")
            try:
                content, sha = await get_file(s, path)
            except Exception as e:
                print(f"  ⚠️ Lỗi tải {path}: {e}")
                failures.append(path)
                continue

            new_content = patch_func(content)
            if new_content != content:
                if apply:
                    print(f"  Đẩy {path} lên GitHub...")
                    await put_file(s, path, new_content, sha, f"Patch workflow {path}")
                else:
                    print(f"  Sẽ thay đổi: {path}")
            else:
                print(f"  ℹ️ {path} đã có patch, bỏ qua.")

    print()
    if failures:
        raise RuntimeError(f"Không tải được {len(failures)} workflow; các file này chưa được vá.")
    print("✅ Hoàn tất." if apply else "✅ Hoàn tất xem trước; chưa có thay đổi trên GitHub.")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Sync fork và ghi workflow lên GitHub")
    parser.add_argument(
        "--skip-sync", action="store_true", help="Chỉ vá workflow, không merge upstream"
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(apply=args.apply, skip_sync=args.skip_sync))
    except Exception as exc:
        parser.exit(1, f"Lỗi: {exc}\n")
