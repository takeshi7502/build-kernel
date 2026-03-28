"""
Sửa các link tải bị lỗi trong web/data/:
1. Thêm GITHUB_OWNER vào URL thiếu tiền tố
2. Kiểm tra và in ra các run_id bị trùng (không tự động xóa - để sếp kiểm tra)
"""
import json
import os
import sys
import glob
from dotenv import load_dotenv

load_dotenv()

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "takeshi7502")
BAD_PREFIX = "https://nightly.link/"
GOOD_REPO_PREFIX = f"https://nightly.link/{GITHUB_OWNER}/"

def fix_file(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    changed = 0
    # Kiểm tra run_id trùng lặp
    run_id_map = {}  # run_id -> [kernel versions sử dụng nó]

    for entry in data.get("entries", []):
        kernel = entry.get("kernel", "")
        downloads = entry.get("downloads", {})
        if not downloads:
            continue

        for variant, link in list(downloads.items()):
            if not link:
                continue
            # Lấy run_id ra khỏi link
            # Format: https://nightly.link/[owner/]REPO/actions/runs/RUN_ID
            parts = link.rstrip("/").split("/")
            run_id = parts[-1]
            run_id_map.setdefault(run_id, []).append(f"{kernel} ({variant})")

            # Kiểm tra xem link có thiếu OWNER không
            # Link đúng: https://nightly.link/takeshi7502/GKI_KernelSU_SUSFS/...
            # Link sai:  https://nightly.link/GKI_KernelSU_SUSFS/...
            after_prefix = link[len(BAD_PREFIX):]
            segments = after_prefix.split("/")
            # Nếu segment đầu tiên là tên repo (không phải owner), URL bị thiếu owner
            if segments[0] != GITHUB_OWNER:
                fixed_link = f"{GOOD_REPO_PREFIX}{after_prefix}"
                print(f"  FIX {kernel} [{variant}]: {link}")
                print(f"    → {fixed_link}")
                downloads[variant] = fixed_link
                changed += 1

        if downloads:
            entry["downloads"] = downloads

    # Báo cáo trùng run_id
    duplicates = {rid: kernels for rid, kernels in run_id_map.items() if len(kernels) > 1}
    if duplicates:
        print(f"\n  ⚠️  Phát hiện run_id bị gán cho nhiều kernel:")
        for rid, kernels in duplicates.items():
            print(f"    run_id={rid} → {kernels}")

    if changed > 0:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [FIXED] Da sua {changed} link trong {os.path.basename(json_path)}")
    else:
        print(f"  [OK] Khong can sua: {os.path.basename(json_path)}")

    return changed

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(script_dir, "web", "data")
    
    json_files = glob.glob(os.path.join(data_dir, "**", "*.json"), recursive=True)
    json_files = [f for f in json_files if "announcement" not in f]
    
    total = 0
    for f in sorted(json_files):
        relative = os.path.relpath(f, script_dir)
        print(f"\n[FILE] {relative}")
        total += fix_file(f)

    print(f"\n{'='*50}")
    print(f"Tong cong da sua: {total} link")

if __name__ == "__main__":
    main()
