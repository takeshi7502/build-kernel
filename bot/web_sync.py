import json
import base64
import logging
from datetime import datetime, timezone
import config

logger = logging.getLogger("gww-web-sync")

_GH_RUNS_CACHE = {}
_LAST_GH_FETCH = 0

async def get_realtime_data(app):
    global _GH_RUNS_CACHE, _LAST_GH_FETCH
    """Trích xuất dữ liệu Real-time trực tiếp từ File và Github API qua RAM (0 delay)"""
    try:
        storage = app.bot_data.get("storage")
        gh = app.bot_data.get("gh")
        if not storage:
            return {"status": "offline", "builds": []}

        data = {
            "status": "online",
            "last_ping": int(datetime.now(timezone.utc).timestamp()),
            "builds": []
        }
        
        jobs = await storage.get_jobs()
        # Sắp xếp mới nhất lên đầu
        jobs = sorted(jobs, key=lambda x: x.get("_id", 0), reverse=True)
        
        # -------- BẮT ĐẦU ĐỒNG BỘ VỚI GITHUB --------
        if gh:
            now = int(datetime.now(timezone.utc).timestamp())
            if now - _LAST_GH_FETCH > 30: # Phân giải cache 30 giây để tránh Rate Limit
                url = f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs?per_page=50"
                res = await gh._request("GET", url)
                if res.get("status") == 200:
                    _GH_RUNS_CACHE = {str(r["id"]): r for r in res.get("json", {}).get("workflow_runs", [])}
                    _LAST_GH_FETCH = now
            
            if _GH_RUNS_CACHE:
                active_jobs = []
                for j in jobs:
                    run_id = str(j.get("run_id", ""))
                    if not run_id or run_id == "None":
                        active_jobs.append(j)
                        continue
                        
                    if run_id not in _GH_RUNS_CACHE:
                        # Không có trong top 50 run gần nhất. Nếu cũ quá 6 tiếng thì báo lỗi để tránh kẹt trạng thái.
                        try:
                            cat = datetime.fromisoformat(j.get("created_at", "").replace("Z", "+00:00"))
                            if (datetime.now(timezone.utc) - cat).total_seconds() > 6 * 3600:
                                if j.get("status") != "completed":
                                    j["status"] = "completed"
                                    j["conclusion"] = "timed_out"
                        except Exception:
                            pass
                        active_jobs.append(j)
                        continue
                        
                    gh_run = _GH_RUNS_CACHE[run_id]
                    # Update status native
                    j["status"] = "completed" if gh_run.get("status") == "completed" else "in_progress"
                    j["conclusion"] = gh_run.get("conclusion")
                    active_jobs.append(j)
                jobs = active_jobs
        # -------- KẾT THÚC ĐỒNG BỘ VỚI GITHUB --------
        
        # Gom nhóm buildsave jobs cùng batch_id thành 1 card
        batch_groups: dict = {}
        standalone_jobs = []
        
        for j in jobs:
            if j.get("type") == "buildsave" and j.get("batch_id"):
                bid = j["batch_id"]
                batch_groups.setdefault(bid, [])
                batch_groups[bid].append(j)
            else:
                standalone_jobs.append(j)
        
        # Tập hợp tất cả job cần render: non-buildsave + 1 đại diện mỗi batch
        # Mỗi batch được đại diện bởi job đầu tiên (batch_index nhỏ nhất)
        batch_representatives = []
        for bid, bjobs in batch_groups.items():
            bjobs.sort(key=lambda x: x.get("batch_index", 0))
            rep = bjobs[0].copy()
            rep["_batch_jobs"] = bjobs  # Đính kèm toàn bộ sub-jobs
            batch_representatives.append(rep)
        
        # Sắp xếp đại diện các batch theo created_at mới nhất
        batch_representatives.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        all_jobs_to_render = sorted(standalone_jobs, key=lambda x: x.get("_id", 0), reverse=True)[:50]
        all_jobs_to_render = (batch_representatives + all_jobs_to_render)[:60]
        
        for j in all_jobs_to_render:
            inputs = j.get("inputs", {})
            user_name = j.get("user_name") or j.get("sender_name") or "Unknown"
            
            if j.get("_batch_jobs"):  # Đây là đại diện của 1 batch buildsave
                bjobs = j["_batch_jobs"]
                variant = j.get("bs_variant", "SukiSU")
                android_v = j.get("bs_android", "").upper()
                
                # Tạo subtitle gộp các sub-levels lại
                completed = sum(1 for bj in bjobs if bj.get("status") == "completed" and bj.get("conclusion") == "success")
                failed = sum(1 for bj in bjobs if bj.get("status") == "completed" and bj.get("conclusion") != "success")
                total = len(bjobs)
                
                # Lấy run_id của job đang active (dispatched/in_progress)
                active_job = next((bj for bj in bjobs if bj.get("status") in ("dispatched", "in_progress", "running")), None)
                run_id = active_job.get("run_id") if active_job else bjobs[-1].get("run_id")
                
                sub_labels = [bj.get("bs_full_ver", "") for bj in bjobs]
                sub_title_str = ", ".join(sub_labels) if len(sub_labels) <= 4 else f"{', '.join(sub_labels[:3])}... (+{len(sub_labels)-3})"
                
                branch = str(inputs.get("kernelsu_branch", "Stable")).replace("(标准)", "").replace("(开发)", "").strip() or "Stable"
                sub_title = f"{android_v} | {sub_title_str} | {branch}"
                custom_version = str(inputs.get("version", "")).strip("-")
                zram = "Bật" if inputs.get("use_zram", True) else "Tắt"
                bbg = "Bật" if inputs.get("use_bbg", True) else "Tắt"
                kpm = "Bật" if inputs.get("use_kpm", True) else "Tắt"
                susfs = "Tắt" if inputs.get("cancel_susfs", True) else "Bật"
                
                # Tính trạng thái tổng hợp của cả batch
                if all(bj.get("status") == "completed" for bj in bjobs):
                    if failed == 0:
                        batch_status = "success"
                    elif completed == 0:
                        batch_status = "failed"
                    else:
                        batch_status = "partial"  # Một số thành công, một số lỗi
                elif any(bj.get("status") == "queued" for bj in bjobs):
                    batch_status = "building"  # Vẫn còn đang chờ
                else:
                    batch_status = "building"
                
                repo_name = j.get("repo", config.GKI_REPO)
                github_link = f"https://github.com/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}" if run_id else "#"
                nightly_link = f"https://nightly.link/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}" if run_id else "#"
                
                progress_str = f"{completed}/{total} xong"
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
                continue
            
            if j.get("type") == "oki":
                # OKI build parsing
                file_val = str(inputs.get("FILE", "Unknown"))
                # Clean label: 'oneplus_ace2_b' -> 'ace2'
                clean_name = file_val
                if clean_name.startswith("oneplus_"): clean_name = clean_name[len("oneplus_"):]
                if clean_name.endswith("_v"): clean_name = clean_name[:-2]
                if clean_name.endswith("_b"): clean_name = clean_name[:-2]
                clean_name = clean_name.replace("_", " ").title()
                
                os_str = f"OnePlus - {clean_name}"
                
                ksu_meta = str(inputs.get("KSU_META", ""))
                if "susfs-main" in ksu_meta: variant = "SukiSU"
                elif "next" in ksu_meta: variant = "NextSU"
                elif "resuki" in ksu_meta: variant = "ReSuKi"
                else: variant = ksu_meta.split("/")[0] if ksu_meta else "NoKSU"
                
                title = variant
                sub_title = os_str
                
                custom_version = inputs.get("SUFFIX", "")
                zram_val = str(inputs.get("ZRAM", "0"))
                zram = "Bật" if zram_val.startswith("1") else "Tắt"
                
                bbg_val = str(inputs.get("LSM_BBG", "true")).lower()
                bbg = "Bật" if bbg_val == "true" else "Tắt"
                
                kpm_val = str(inputs.get("KPM", "KPM"))
                kpm = kpm_val if kpm_val in ["KPM", "KPN"] else "Tắt"
                
                susfs_val = str(inputs.get("SUSFS_CI", "N/A"))
                susfs = "Tắt" if susfs_val == "N/A" else "Bật"
                
            elif j.get("type") == "buildsave":
                # Buildsave đơn lẻ (không có batch_id) - fallback
                variant = j.get("bs_variant", "SukiSU")
                title = variant
                
                android_v = j.get("bs_android", "").upper()
                full_ver = j.get("bs_full_ver", "")
                os_str = f"{android_v}-{full_ver}"
                
                branch = str(inputs.get("kernelsu_branch", "Stable")).replace("(标准)", "").replace("(开发)", "").strip()
                if not branch: branch = "Stable"
                sub_title = f"{os_str}-{branch}"
                
                custom_version = str(inputs.get("version", "")).strip("-")
                zram = "Bật" if inputs.get("use_zram", True) else "Tắt"
                bbg = "Bật" if inputs.get("use_bbg", True) else "Tắt"
                kpm = "Bật" if inputs.get("use_kpm", True) else "Tắt"
                susfs = "Tắt" if inputs.get("cancel_susfs", True) else "Bật"
                
            else:
                # Normal GKI parsing
                os_list = []
                if inputs.get("build_all"):
                    os_list = ["ALL versions"]
                else:
                    if inputs.get("build_a12_5_10"): os_list.append("A12-5.10")
                    if inputs.get("build_a13_5_15"): os_list.append("A13-5.15")
                    if inputs.get("build_a14_6_1"): os_list.append("A14-6.1")
                    if inputs.get("build_a15_6_6"): os_list.append("A15-6.6")
                    if inputs.get("build_a16_6_12"): os_list.append("A16-6.12")
                
                os_str = os_list[0] if os_list else "Custom"
                sub = str(inputs.get("sub_levels", "")).replace(",", ".")
                if sub and sub.lower() not in ["all", "*"]:
                    os_str = f"{os_str}.{sub}"
                    
                variant = inputs.get("kernelsu_variant", "NoKSU")
                branch = str(inputs.get("kernelsu_branch", "Stable")).replace("(标准)", "").replace("(开发)", "").strip()
                if not branch: branch = "Stable"
                    
                title = variant
                sub_title = f"{os_str}-{branch}"
                if inputs.get("supp_op"):
                    sub_title += " (8E)"
                
                custom_version = str(inputs.get("version", "")).strip("-")
                zram = "Bật" if inputs.get("use_zram", True) else "Tắt"
                bbg = "Bật" if inputs.get("use_bbg", True) else "Tắt"
                kpm = "Bật" if inputs.get("use_kpm", True) else "Tắt"
                susfs = "Tắt" if inputs.get("cancel_susfs", True) else "Bật"
            
            status = "building"
            if j.get("status") == "completed":
                status = "success" if j.get("conclusion") == "success" else "failed"
                
            run_id = j.get("run_id")
            repo_name = j.get("repo", config.GKI_REPO)
            github_link = f"https://github.com/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}" if run_id else "#"
            nightly_link = f"https://nightly.link/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}" if run_id else "#"
            
            b = {
                "id": str(run_id or j.get("_id", "TBD")),
                "type": j.get("type", "gki"),
                "title": title,
                "sub_title": sub_title,
                "custom_version": custom_version,
                "zram": zram,
                "kpm": kpm,
                "bbg": bbg,
                "susfs": susfs,
                "status": status,
                "date": j.get("created_at"),
                "user_name": user_name,
                "github_link": github_link,
                "nightly_link": nightly_link
            }
            data["builds"].append(b)
            
        return data
    except Exception as e:
        logger.error("Error in get_realtime_data: %s", e)
        return {"status": "error", "message": str(e), "builds": []}
