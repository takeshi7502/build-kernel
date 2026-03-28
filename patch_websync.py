import re

with open('bot/web_sync.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find & replace the batch card block
old_block = '''                progress_str = f"{completed}/{total} xong"
                if failed: progress_str += f", {failed} lỗi"
                
                b = {
                    "id": str(j.get("batch_id", j.get("_id", "TBD"))),
                    "type": "buildsave",
                    "title": variant,
                    "sub_title": sub_title,
                    "custom_version": f"Queue: {progress_str}",
                    "zram": zram, "kpm": kpm, "bbg": bbg, "susfs": susfs,
                    "status": batch_status,
                    "date": j.get("created_at"),
                    "user_name": user_name,
                    "github_link": github_link,
                    "nightly_link": nightly_link
                }
                data["builds"].append(b)
                continue'''

new_block = '''                android_label = j.get("bs_android", "").replace("android", "Android")

                # sub_items: danh sach tung version va status rieng
                sub_items = []
                for bj in bjobs:
                    bj_status_raw = bj.get("status", "queued")
                    bj_conclusion = bj.get("conclusion", "")
                    if bj_status_raw == "completed":
                        bj_st = "success" if bj_conclusion == "success" else "failed"
                    elif bj_status_raw in ("dispatched", "in_progress", "running"):
                        bj_st = "building"
                    else:
                        bj_st = "queued"
                    sub_items.append({"ver": bj.get("bs_full_ver", ""), "status": bj_st})

                b = {
                    "id": str(j.get("batch_id", j.get("_id", "TBD"))),
                    "type": "buildsave",
                    "title": variant,
                    "sub_title": f"{android_label} | Ti\u1ebfn tr\u00ecnh {completed}/{total}",
                    "custom_version": "",
                    "zram": zram, "kpm": kpm, "bbg": bbg, "susfs": susfs,
                    "status": batch_status,
                    "date": j.get("created_at"),
                    "user_name": user_name,
                    "github_link": github_link,
                    "nightly_link": nightly_link,
                    "sub_items": sub_items
                }
                data["builds"].append(b)
                continue'''

if old_block in content:
    content = content.replace(old_block, new_block)
    with open('bot/web_sync.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: patched web_sync.py")
else:
    print("ERROR: old block not found!")
    # Debug: print around the area
    idx = content.find('progress_str')
    print("Found 'progress_str' at index:", idx)
    print("Context:", repr(content[max(0,idx-50):idx+200]))
