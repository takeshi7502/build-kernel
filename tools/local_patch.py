"""Audit modern workflows in a local clone; legacy auto-patching is retired."""

import argparse
import difflib
import sys
from pathlib import Path

try:
    from .sync_and_patch import patch_build_yml, patch_kernel_yml, patch_main_yml
except ImportError:
    from sync_and_patch import patch_build_yml, patch_kernel_yml, patch_main_yml


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path, help="Đường dẫn local clone của repository kernel")
    parser.add_argument(
        "--apply", action="store_true", help="Deprecated; audit never writes workflows"
    )
    args = parser.parse_args()
    if args.apply:
        parser.error(
            "Auto-patching đã ngừng. Chạy không có --apply để kiểm tra, sửa workflow tại repo build."
        )
    workflow_dir = args.repository.resolve() / ".github" / "workflows"
    if not workflow_dir.is_dir():
        parser.error(f"Không tìm thấy thư mục workflow: {workflow_dir}")

    patchers = {"build.yml": patch_build_yml, "main.yml": patch_main_yml}
    for name in (
        "kernel-a12-5-10.yml",
        "kernel-a13-5-15.yml",
        "kernel-a14-6-1.yml",
        "kernel-a15-6-6.yml",
        "kernel-a16-6-12.yml",
    ):
        patchers[name] = patch_kernel_yml
    found = 0
    for name, patch in patchers.items():
        path = workflow_dir / name
        if not path.is_file():
            continue
        found += 1
        original = path.read_text(encoding="utf-8")
        updated = patch(original)
        if updated == original:
            continue
        print(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True), updated.splitlines(True), fromfile=name, tofile=name
                )
            ),
            end="",
        )
        if args.apply:
            path.write_text(updated, encoding="utf-8")
    if not found:
        parser.error("Không tìm thấy workflow GKI được hỗ trợ.")
    print("Kiểm tra thành công; không sửa workflow hay loại runner.")


if __name__ == "__main__":
    main()
