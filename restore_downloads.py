#!/usr/bin/env python3
"""
Khôi phục link tải xuống từ data.json vào các file JSON của web.
Chạy từ thư mục gốc của project: python restore_downloads.py
"""

import json
import os

# Đường dẫn
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_JSON  = os.path.join(SCRIPT_DIR, "data.json")
WEB_DATA   = os.path.join(SCRIPT_DIR, "web", "data")
GITHUB_OWNER = None  # will be detected from jobs

# --- Đọc jobs ---
with open(DATA_JSON, "r", encoding="utf-8") as f:
    db = json.load(f)

jobs = db.get("jobs", [])

# Lọc buildsave đã hoàn thành thành công và có download info
restored = 0
skipped  = 0

for j in jobs:
    if j.get("type") != "buildsave":
        continue
    if j.get("status") != "completed":
        continue
    if j.get("conclusion") != "success":
        continue

    run_id   = j.get("run_id")
    variant  = j.get("bs_variant", "")
    android  = j.get("bs_android", "")       # e.g. android12
    kernel_v = j.get("bs_kernel_ver", "")    # e.g. 5.10
    sub_level= j.get("bs_sub_level", "")     # e.g. 149
    full_ver = j.get("bs_full_ver", "")      # e.g. 5.10.149
    repo     = j.get("repo", "")

    if not all([run_id, variant, android, kernel_v, full_ver, repo]):
        skipped += 1
        continue

    # Xây link nightly
    # repo = "username/reponame" hoặc chỉ "reponame"
    if "/" in repo:
        owner_repo = repo
    else:
        # Lấy owner từ 1 job khác có đủ thông tin
        owner_repo = repo  # fallback

    nightly_link = (
        f"https://nightly.link/{owner_repo}/actions/runs/{run_id}"
    )

    # Mở file JSON tương ứng
    json_path = os.path.join(WEB_DATA, android, f"{kernel_v}.json")
    if not os.path.exists(json_path):
        print(f"  ⚠ Không tìm thấy: {json_path}")
        skipped += 1
        continue

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = False
    for entry in data.get("entries", []):
        if entry.get("kernel") == full_ver:
            if "downloads" not in entry:
                entry["downloads"] = {}
            if variant not in entry["downloads"] or not entry["downloads"][variant]:
                entry["downloads"][variant] = nightly_link
                updated = True
                print(f"  ✅ {full_ver} [{variant}] → {nightly_link}")

    if updated:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        restored += 1
    else:
        skipped += 1

print(f"\nHoàn tất: {restored} entries đã được khôi phục, {skipped} bỏ qua.")
