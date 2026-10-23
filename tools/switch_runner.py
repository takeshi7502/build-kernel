"""Switch local workflow runner labels using YAML rather than text replacement."""

import argparse
import difflib
import sys
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML


def switch_runner(content, target):
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    data = yaml.load(content)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return content
    changed = False
    for name, job in data["jobs"].items():
        if (
            not isinstance(job, dict)
            or "runs-on" not in job
            or str(name).startswith(("setup-", "prepare-"))
        ):
            continue
        # Preserve custom labels, expressions, OS-specific jobs and label arrays.
        if (
            job["runs-on"] in ("ubuntu-latest", "ubuntu-24.04", "self-hosted")
            and job["runs-on"] != target
        ):
            job["runs-on"] = target
            changed = True
    if not changed:
        return content
    result = StringIO()
    yaml.dump(data, result)
    return result.getvalue()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("github", "vps"))
    parser.add_argument("repository", type=Path, help="Đường dẫn local clone của repository kernel")
    parser.add_argument("--apply", action="store_true", help="Ghi file; mặc định chỉ in diff")
    args = parser.parse_args()
    directory = args.repository.resolve() / ".github" / "workflows"
    files = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])
    if not files:
        parser.error(f"Không tìm thấy workflow trong {directory}")
    target = "self-hosted" if args.target == "vps" else "ubuntu-24.04"
    for path in files:
        original = path.read_text(encoding="utf-8")
        updated = switch_runner(original, target)
        if updated != original:
            print(
                "".join(
                    difflib.unified_diff(
                        original.splitlines(True),
                        updated.splitlines(True),
                        fromfile=path.name,
                        tofile=path.name,
                    )
                ),
                end="",
            )
            if args.apply:
                path.write_text(updated, encoding="utf-8")
    print("Đã ghi thay đổi." if args.apply else "Xem trước; thêm --apply để ghi thay đổi.")


if __name__ == "__main__":
    main()
