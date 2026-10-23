"""Recover dashboard links from recent GitHub artifacts (preview by default)."""

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


def gh_api(url, token):
    headers = {"User-Agent": "kernel-bot-maintenance", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitHub trả HTTP {exc.code}; kiểm tra token, quyền truy cập hoặc rate limit."
        ) from exc


def recover_links(android, kernel, filepath, owner, repo, token, *, apply=False):
    data = json.loads(filepath.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("File dashboard phải chứa danh sách entries.")
    repository = f"{owner}/{repo}"
    url = f"https://api.github.com/repos/{repository}/actions/runs?status=success&event=workflow_dispatch&per_page=100"
    runs = gh_api(url, token).get("workflow_runs", [])
    pattern = re.compile(
        r"^(SukiSU(?:\(\d+\))?|BakaSU|ReSukiSU|KittiSU|Official|MKSU|Next)_kernel-"
        + re.escape(android)
        + "-"
        + re.escape(kernel)
        + r"-(\d+)(?:\D|$)"
    )
    found = {}
    for run in runs:
        artifacts = gh_api(
            f"https://api.github.com/repos/{repository}/actions/runs/{int(run['id'])}/artifacts?per_page=100",
            token,
        )
        for artifact in artifacts.get("artifacts", []):
            if artifact.get("expired"):
                continue
            match = pattern.match(artifact.get("name", ""))
            if match:
                # API returns newest first: keep the newest run for each variant.
                found.setdefault(f"{kernel}.{match.group(2)}", {}).setdefault(
                    match.group(1), run["id"]
                )
    changed = 0
    for entry in data["entries"]:
        for variant, run_id in found.get(entry.get("kernel"), {}).items():
            downloads = entry.setdefault("downloads", {})
            link = f"https://nightly.link/{repository}/actions/runs/{run_id}"
            if downloads.get(variant) != link:
                downloads[variant] = link
                changed += 1
                print(f"{entry['kernel']} / {variant} -> {link}")
    if apply and changed:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=filepath.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, filepath)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(
        f"{'Đã cập nhật' if apply else 'Sẽ cập nhật'} {changed} link; chỉ xét 100 run thành công gần nhất."
    )


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("android", help="Ví dụ: android12")
    parser.add_argument("kernel", help="Ví dụ: 5.10")
    parser.add_argument("file", type=Path, help="File web/data/...json cần khôi phục")
    parser.add_argument("--owner")
    parser.add_argument("--repo")
    parser.add_argument("--apply", action="store_true", help="Ghi file; mặc định chỉ xem trước")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    owner = args.owner or os.getenv("GITHUB_OWNER", "").strip()
    repo = args.repo or os.getenv("GKI_REPO", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        parser.error("Cần GITHUB_OWNER và GKI_REPO trong .env hoặc --owner/--repo hợp lệ.")
    try:
        recover_links(
            args.android,
            args.kernel,
            args.file.resolve(),
            owner,
            repo,
            os.getenv("GITHUB_TOKEN", "").strip(),
            apply=args.apply,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Lỗi: {exc}\n")


if __name__ == "__main__":
    main()
