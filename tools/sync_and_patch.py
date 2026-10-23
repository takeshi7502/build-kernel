"""Read-only audit of modern GKI workflow contracts.

Legacy auto-patching was retired: it could overwrite dynamic matrices and
silently move builds to self-hosted runners. Sync the build repo with Git and
review conflicts there. This tool never merges, commits or changes workflows.
"""

import argparse
import asyncio
import base64
import sys
from pathlib import Path

import aiohttp
from ruamel.yaml import YAML


def _load(content):
    data = YAML(typ="safe").load(content)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise ValueError("Workflow must contain jobs")
    return data


def patch_build_yml(content):
    """Compatibility entry point: validate only, return original source."""
    data = _load(content)
    fields = data.get("on", {}).get("workflow_call", {}).get("inputs", {})
    if not {"ksu_mode", "use_nomount", "use_networking", "droidspaces_ntsync"} <= set(fields):
        raise ValueError(
            "Old build contract: manually integrate the reviewed upstream update first"
        )
    return content


def patch_main_yml(content):
    data = _load(content)
    fields = data.get("on", {}).get("workflow_dispatch", {}).get("inputs", {})
    if not {"ksu_mode", "sub_levels", "artifact_upload_mode", "export_susfs_patches"} <= set(
        fields
    ):
        raise ValueError(
            "Main workflow is missing the modern bot contract; update the build fork manually"
        )
    if data.get("concurrency"):
        raise ValueError("Review main concurrency: independent users must not cancel each other")
    return content


def patch_kernel_yml(content):
    data = _load(content)
    jobs = data["jobs"]
    if "prepare-matrix" not in jobs:
        raise ValueError(
            "Missing prepare-matrix: integrate the modern build fork instead of legacy patches"
        )
    calls = [job for job in jobs.values() if job.get("uses") == "./.github/workflows/build.yml"]
    if not calls or any(
        not isinstance(job.get("strategy", {}).get("matrix"), str) for job in calls
    ):
        raise ValueError("Expected the reviewed dynamic matrix; no automatic rewrite was attempted")
    for job in calls:
        if "sub_levels" in job.get("with", {}):
            raise ValueError(
                "sub_levels belongs to prepare-matrix, not the root build reusable workflow"
            )
    return content


async def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bot import config

    root = f"https://api.github.com/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/contents"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
    }
    patchers = {"build.yml": patch_build_yml, "main.yml": patch_main_yml}
    for version in ("a12-5-10", "a13-5-15", "a14-6-1", "a15-6-6", "a16-6-12"):
        patchers[f"kernel-{version}.yml"] = patch_kernel_yml
    failures = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        for name, validate in patchers.items():
            path = f".github/workflows/{name}"
            try:
                async with session.get(
                    f"{root}/{path}", headers=headers, params={"ref": config.GKI_DEFAULT_BRANCH}
                ) as response:
                    body = await response.json()
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status}")
                    validate(base64.b64decode(body["content"]).decode("utf-8"))
                print(f"OK {path}")
            except (aiohttp.ClientError, ValueError, KeyError, TimeoutError) as error:
                failures.append(name)
                print(f"FAIL {path}: {error}")
    if failures:
        raise ValueError(f"{len(failures)} workflow(s) need manual review; no remote changes made")
    print("Contract audit passed. No workflows, runner labels or GitHub refs changed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Retired; no remote writes are supported"
    )
    parser.add_argument("--skip-sync", action="store_true", help="Legacy flag; audit never syncs")
    args = parser.parse_args()
    if args.apply:
        parser.error(
            "--apply was retired. Merge/review the build repo with Git; use this tool only to audit."
        )
    try:
        asyncio.run(main())
    except (ValueError, OSError) as error:
        parser.exit(1, f"Audit failed: {error}\n")
