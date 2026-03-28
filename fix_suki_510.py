"""
Gán lại đúng run_id SukiSU cho từng kernel 5.10.x trong web/data/android12/5.10.json
Dựa trên dữ liệu thực tế từ data.json (jobs database)
"""
import json
import os

GITHUB_OWNER = "takeshi7502"
GKI_REPO = "GKI_KernelSU_SUSFS"

# Bảng mapping chính xác: kernel_version -> run_id đúng của SukiSU
# Lấy từ data.json jobs, loại bỏ run_id 23666183967 (bị gán nhầm hàng loạt)
CORRECT_SUKI_RUNS = {
    "5.10.66":  23663678469,
    "5.10.81":  23664172338,
    "5.10.101": 23666183967,   # job đầu tiên của batch, đây là run_id gốc đúng
    "5.10.110": 23666985023,
    "5.10.117": 23667366517,
    "5.10.136": 23667691772,
    "5.10.149": 23668016121,
    "5.10.160": 23668328778,
    "5.10.168": 23668627930,
    "5.10.177": 23668928417,
    "5.10.185": 23669555747,
    "5.10.198": 23669833976,
    "5.10.205": 23670103631,
    "5.10.209": 23670722729,
    "5.10.218": 23671252386,
    "5.10.226": 23671498694,
    "5.10.233": 23671733883,
    "5.10.236": 23671963655,
    "5.10.237": 23672195932,
    "5.10.240": 23672663470,
    "5.10.246": 23672866822,
}

def make_link(run_id):
    return f"https://nightly.link/{GITHUB_OWNER}/{GKI_REPO}/actions/runs/{run_id}"

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(script_dir, "web", "data", "android12", "5.10.json")
    json_path = os.path.normpath(json_path)

    if not os.path.exists(json_path):
        print(f"[ERROR] Khong tim thay: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    fixed = 0
    for entry in data.get("entries", []):
        kernel = entry.get("kernel", "")
        if kernel not in CORRECT_SUKI_RUNS:
            continue

        correct_run_id = CORRECT_SUKI_RUNS[kernel]
        correct_link = make_link(correct_run_id)

        if "downloads" not in entry:
            entry["downloads"] = {}

        old_link = entry["downloads"].get("SukiSU", "")
        if old_link != correct_link:
            print(f"  FIX {kernel}: SukiSU")
            print(f"    OLD: {old_link}")
            print(f"    NEW: {correct_link}")
            entry["downloads"]["SukiSU"] = correct_link
            fixed += 1
        else:
            print(f"  OK  {kernel}: SukiSU (khong doi)")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDa sua {fixed} entry. File da duoc cap nhat: {json_path}")

if __name__ == "__main__":
    main()
