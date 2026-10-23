import html
import logging
import time
from datetime import datetime, timedelta, timezone

from . import config
from .gki_contract import MODE_NO_KSU, effective_mode, kpm_enabled, mode_label, susfs_enabled
from .status import cached_run


def _enabled(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "on", "enabled"}


def _is_kpm_enabled(inputs: dict) -> bool:
    return kpm_enabled(inputs)


def _make_custom_id(variant, inputs, created_at_iso):
    if not created_at_iso:
        created_at_iso = ""
    var_cl = (
        str(variant).upper().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")[:6]
    )
    z_n = "1" if _enabled(inputs.get("use_zram"), True) else "0"
    b_n = "1" if _enabled(inputs.get("use_bbg"), True) else "0"
    k_n = "1" if _is_kpm_enabled(inputs) else "0"
    s_n = "1" if susfs_enabled(inputs) else "0"
    try:
        dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00")) + timedelta(hours=7)
        time_str = dt.strftime("%H%M.%d%m%y")
    except Exception:
        time_str = "0000.000000"
    return f"{var_cl}{z_n}{b_n}{k_n}{s_n}.{time_str}"


logger = logging.getLogger("gww-web-sync")


def _html_text(value) -> str:
    """Escape values that are inserted into the dashboard's HTML templates."""
    return html.escape(str(value if value is not None else ""))


async def get_realtime_data(app):
    """Render storage with cached GitHub status; never invent missing-run failures."""
    try:
        storage = app.bot_data.get("storage")
        if not storage:
            return {"status": "offline", "builds": []}

        data = {
            "status": "online",
            "last_ping": int(datetime.now(timezone.utc).timestamp()),
            "builds": [],
        }
        supervisor = app.bot_data.get("supervisor")
        if supervisor and supervisor.health()["status"] != "ok":
            data["status"] = "degraded"

        jobs = await storage.get_jobs()
        # Sắp xếp mới nhất lên đầu
        jobs = sorted(jobs, key=lambda x: x.get("_id", 0), reverse=True)

        # API requests render a snapshot only. The shared worker fetches GitHub.
        app.bot_data["dashboard_last_request"] = time.monotonic()
        data["github_last_sync"] = app.bot_data.get("github_last_sync")
        for job in jobs:
            run = cached_run(app, job.get("repo", config.GKI_REPO), job.get("run_id"))
            if run and job.get("status") not in {
                "completed",
                "queued",
                "rebuild_queued",
                "dispatching",
            }:
                job["status"] = "completed" if run.get("status") == "completed" else "in_progress"
                job["conclusion"] = run.get("conclusion")

        data["active_builds"] = sum(
            job.get("status")
            in {
                "dispatched",
                "running",
                "in_progress",
                "queued",
                "dispatching",
                "rebuild_queued",
                "waiting",
                "pending",
            }
            for job in jobs
        )
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

        all_jobs_to_render = sorted(standalone_jobs, key=lambda x: x.get("_id", 0), reverse=True)
        all_jobs_to_render = batch_representatives + all_jobs_to_render

        for j in all_jobs_to_render:
            inputs = {
                key: _html_text(value) if isinstance(value, str) else value
                for key, value in j.get("inputs", {}).items()
            }
            user_name = _html_text(j.get("user_name") or j.get("sender_name") or "Unknown")

            if j.get("_batch_jobs"):  # Đây là đại diện của 1 batch buildsave
                bjobs = j["_batch_jobs"]
                variant = _html_text(
                    "GKI thuần"
                    if effective_mode(inputs) == MODE_NO_KSU
                    else j.get("bs_variant", "SukiSU")
                )
                android_v = _html_text(j.get("bs_android", "").upper())

                # Tạo subtitle gộp các sub-levels lại
                completed = sum(
                    1
                    for bj in bjobs
                    if bj.get("status") == "completed" and bj.get("conclusion") == "success"
                )
                failed = sum(
                    1
                    for bj in bjobs
                    if bj.get("status") == "completed" and bj.get("conclusion") != "success"
                )
                total = len(bjobs)

                # Lấy run_id của job đang active (dispatched/in_progress)
                active_job = next(
                    (
                        bj
                        for bj in bjobs
                        if bj.get("status") in ("dispatched", "in_progress", "running")
                    ),
                    None,
                )
                run_id = active_job.get("run_id") if active_job else bjobs[-1].get("run_id")

                sub_labels = [bj.get("bs_full_ver", "") for bj in bjobs]
                sub_title_str = (
                    ", ".join(sub_labels)
                    if len(sub_labels) <= 4
                    else f"{', '.join(sub_labels[:3])}... (+{len(sub_labels) - 3})"
                )

                branch = mode_label(inputs)
                sub_title = f"{android_v} | {sub_title_str} | {branch}"
                custom_version = str(inputs.get("version", "")).strip("-")
                zram = "Bật" if _enabled(inputs.get("use_zram"), True) else "Tắt"
                bbg = "Bật" if _enabled(inputs.get("use_bbg"), True) else "Tắt"
                kpm = "Bật" if _is_kpm_enabled(inputs) else "Tắt"
                susfs = "Bật" if susfs_enabled(inputs) else "Tắt"

                # Tính trạng thái tổng hợp của cả batch
                if all(bj.get("status") == "completed" for bj in bjobs):
                    if any(bj.get("conclusion") == "unknown" for bj in bjobs):
                        batch_status = "unknown"
                    elif failed == 0:
                        batch_status = "success"
                    elif completed == 0:
                        batch_status = "failed"
                    else:
                        batch_status = "partial"  # Một số thành công, một số lỗi
                elif any(bj.get("status") in ("queued", "rebuild_queued") for bj in bjobs):
                    batch_status = "building"  # Vẫn còn đang chờ
                else:
                    batch_status = "building"

                repo_name = j.get("repo", config.GKI_REPO)
                github_link = (
                    f"https://github.com/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}"
                    if run_id
                    else "#"
                )
                nightly_link = (
                    f"https://nightly.link/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}"
                    if run_id
                    else "#"
                )

                android_label = _html_text(j.get("bs_android", "").replace("android", "Android"))

                # sub_items: danh sach tung version va status rieng
                sub_items = []
                total_batch_seconds = 0
                import re

                for bj in bjobs:
                    bj_status_raw = bj.get("status", "queued")
                    bj_conclusion = bj.get("conclusion", "")
                    if bj_status_raw == "completed":
                        if bj_conclusion == "success":
                            bj_st = "success"
                        elif bj_conclusion in ("cancelled", "stale"):
                            bj_st = "cancelled"
                        elif bj_conclusion == "unknown":
                            bj_st = "unknown"
                        else:
                            bj_st = "failed"
                    elif bj_status_raw in ("dispatched", "in_progress", "running"):
                        bj_st = "building"
                    elif bj_status_raw == "rebuild_queued":
                        if bj_conclusion in ("cancelled", "stale"):
                            bj_st = "cancelled"
                        else:
                            bj_st = "failed"
                    else:
                        bj_st = "queued"

                    bj_dur = bj.get("gh_duration", "")
                    run_id = str(bj.get("run_id", ""))
                    is_added_from_cache = False

                    r = (
                        bj.get("github_result")
                        if bj.get("github_completed")
                        else cached_run(app, bj.get("repo", config.GKI_REPO), run_id)
                    )
                    if r:
                        try:
                            t1_str = r.get("run_started_at") or r.get("created_at", "")
                            if t1_str:
                                t1 = datetime.fromisoformat(t1_str.replace("Z", "+00:00"))
                                t2_str = r.get("updated_at")
                                if r.get("status") == "completed" and t2_str:
                                    t2 = datetime.fromisoformat(t2_str.replace("Z", "+00:00"))
                                    diff = int((t2 - t1).total_seconds())
                                    if diff > 0:
                                        total_batch_seconds += diff
                                        is_added_from_cache = True
                                        bj_dur = f"{diff // 60}m {diff % 60}s"
                                else:
                                    t2 = datetime.now(timezone.utc)
                                    diff = int((t2 - t1).total_seconds())
                                    if diff > 0:
                                        bj_dur = f"{diff // 60}m {diff % 60}s"
                        except Exception:
                            pass

                    # Parse duration from string if it wasn't fetched from cache and job is completed
                    if not is_added_from_cache and bj_status_raw == "completed" and bj_dur:
                        try:
                            sec = 0
                            hm = re.search(r"(\d+)\s*h", bj_dur)
                            if hm:
                                sec += int(hm.group(1)) * 3600
                            mm = re.search(r"(\d+)\s*m", bj_dur)
                            if mm:
                                sec += int(mm.group(1)) * 60
                            sm = re.search(r"(\d+)\s*s", bj_dur)
                            if sm:
                                sec += int(sm.group(1))
                            total_batch_seconds += sec
                        except Exception:
                            pass

                    sub_items.append(
                        {
                            "ver": _html_text(bj.get("bs_full_ver", "")),
                            "status": bj_st,
                            "duration": _html_text(bj_dur),
                        }
                    )

                # Ẩn card nếu tất cả đã done nhưng không có cái nào success (toàn cancel/fail)
                all_done_batch = all(bj.get("status") == "completed" for bj in bjobs)
                if all_done_batch and completed == 0 and batch_status != "unknown":
                    continue  # Xoá khỏi web, không hiển thị card thất bại/cancel hoàn toàn

                _cid = _make_custom_id(variant, inputs, j.get("created_at", ""))

                batch_dur_str = ""
                if total_batch_seconds > 0:
                    h = total_batch_seconds // 3600
                    m = (total_batch_seconds % 3600) // 60
                    s = total_batch_seconds % 60
                    if h > 0:
                        batch_dur_str = f"{h}h {m}m {s}s"
                    else:
                        batch_dur_str = f"{m}m {s}s"

                b = {
                    "id": str(j.get("batch_id", j.get("_id", "TBD"))),
                    "type": "buildsave",
                    "title": variant,
                    "sub_title": f"{android_label} | Tiến trình {completed}/{total}<br><span style='font-family: monospace; font-size: 0.78rem; color: var(--text-muted); opacity: 0.8;'>ID: {_cid}</span>",
                    "custom_version": "",
                    "zram": zram,
                    "kpm": kpm,
                    "bbg": bbg,
                    "susfs": susfs,
                    "ksu_mode": effective_mode(inputs) if j.get("type") != "oki" else "",
                    "nomount": _enabled(inputs.get("use_nomount")),
                    "networking": _enabled(inputs.get("use_networking")),
                    "ntsync": _enabled(inputs.get("droidspaces_ntsync")),
                    "cve_2026_43499_patch": _enabled(inputs.get("cve_2026_43499_patch")),
                    "status": batch_status,
                    "duration": batch_dur_str,
                    "date": j.get("created_at"),
                    "user_name": user_name,
                    "github_link": github_link,
                    "nightly_link": nightly_link,
                    "sub_items": sub_items,
                }
                data["builds"].append(b)
                continue

            if j.get("type") == "oki":
                # OKI build parsing
                file_val = str(inputs.get("FILE", "Unknown"))
                # Clean label: 'oneplus_ace2_b' -> 'ace2'
                clean_name = file_val
                if clean_name.startswith("oneplus_"):
                    clean_name = clean_name[len("oneplus_") :]
                if clean_name.endswith("_v"):
                    clean_name = clean_name[:-2]
                if clean_name.endswith("_b"):
                    clean_name = clean_name[:-2]
                clean_name = clean_name.replace("_", " ").title()

                os_str = f"OnePlus - {clean_name}"

                ksu_meta = str(inputs.get("KSU_META", ""))
                ksu_parts = ksu_meta.split("/")
                ksu_manager_branch = ksu_parts[0].lower() if ksu_parts else ""
                ksu_builtin_branch = ksu_parts[1].lower() if len(ksu_parts) > 1 else ""
                if ksu_manager_branch == "kittisu":
                    variant = "KittiSU"
                elif ksu_builtin_branch == "next":
                    variant = "NextSU"
                elif ksu_builtin_branch == "resuki":
                    variant = "ReSuKi"
                elif ksu_manager_branch in {"main", "susfs-main", "builtin"}:
                    variant = "SukiSU"
                else:
                    variant = ksu_manager_branch or "NoKSU"

                title = variant
                run_id_str = str(j.get("run_id")) if j.get("run_id") else "Đang chờ..."
                sub_title = f"{os_str}<br><span style='font-family: monospace; font-size: 0.78rem; color: var(--text-muted); opacity: 0.8;'>Run ID: {run_id_str}</span>"

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
                variant = _html_text(
                    "GKI thuần"
                    if effective_mode(inputs) == MODE_NO_KSU
                    else j.get("bs_variant", "SukiSU")
                )
                title = variant

                android_v = _html_text(j.get("bs_android", "").upper())
                full_ver = _html_text(j.get("bs_full_ver", ""))
                os_str = f"{android_v}-{full_ver}"

                branch = mode_label(inputs)
                if not branch:
                    branch = "Stable"
                run_id_str = str(j.get("run_id")) if j.get("run_id") else "Đang chờ..."
                sub_title = f"{os_str}-{branch}<br><span style='font-family: monospace; font-size: 0.78rem; color: var(--text-muted); opacity: 0.8;'>Run ID: {run_id_str}</span>"

                custom_version = str(inputs.get("version", "")).strip("-")
                zram = "Bật" if _enabled(inputs.get("use_zram"), True) else "Tắt"
                bbg = "Bật" if _enabled(inputs.get("use_bbg"), True) else "Tắt"
                kpm = "Bật" if _is_kpm_enabled(inputs) else "Tắt"
                susfs = "Bật" if susfs_enabled(inputs) else "Tắt"

            else:
                # Normal GKI parsing
                os_list = []
                if _enabled(inputs.get("build_all")):
                    os_list = ["ALL versions"]
                else:
                    if _enabled(inputs.get("build_a12_5_10")):
                        os_list.append("A12-5.10")
                    if _enabled(inputs.get("build_a13_5_15")):
                        os_list.append("A13-5.15")
                    if _enabled(inputs.get("build_a14_6_1")):
                        os_list.append("A14-6.1")
                    if _enabled(inputs.get("build_a15_6_6")):
                        os_list.append("A15-6.6")
                    if _enabled(inputs.get("build_a16_6_12")):
                        os_list.append("A16-6.12")

                os_str = os_list[0] if os_list else "Custom"
                sub = str(inputs.get("sub_levels", "")).replace(",", ".")
                if sub and sub.lower() not in ["all", "*"]:
                    os_str = f"{os_str}.{sub}"

                variant = (
                    "GKI thuần"
                    if effective_mode(inputs) == MODE_NO_KSU
                    else inputs.get("kernelsu_variant", "NoKSU")
                )
                branch = mode_label(inputs)
                if not branch:
                    branch = "Stable"

                title = variant
                run_id_str = str(j.get("run_id")) if j.get("run_id") else "Đang chờ..."
                sub_title = f"{os_str}-{branch}<br><span style='font-family: monospace; font-size: 0.78rem; color: var(--text-muted); opacity: 0.8;'>Run ID: {run_id_str}</span>"
                if _enabled(inputs.get("supp_op")):
                    sub_title += " (8E)"

                custom_version = str(inputs.get("version", "")).strip("-")
                zram = "Bật" if _enabled(inputs.get("use_zram"), True) else "Tắt"
                bbg = "Bật" if _enabled(inputs.get("use_bbg"), True) else "Tắt"
                kpm = "Bật" if _is_kpm_enabled(inputs) else "Tắt"
                susfs = "Bật" if susfs_enabled(inputs) else "Tắt"

            status = "building"
            if j.get("status") == "completed":
                status = (
                    "success"
                    if j.get("conclusion") == "success"
                    else "unknown"
                    if j.get("conclusion") == "unknown"
                    else "failed"
                )
            elif j.get("status") in {"queued", "rebuild_queued"}:
                status = "queued"

            duration = j.get("gh_duration", "")
            run_id = str(j.get("run_id", ""))
            repo_name = j.get("repo", config.GKI_REPO)
            r = (
                j.get("github_result")
                if j.get("github_completed")
                else cached_run(app, repo_name, run_id)
            )
            if r:
                try:
                    t1_str = r.get("run_started_at") or r.get("created_at", "")
                    if t1_str:
                        t1 = datetime.fromisoformat(t1_str.replace("Z", "+00:00"))
                        t2_str = r.get("updated_at")
                        if r.get("status") == "completed" and t2_str:
                            t2 = datetime.fromisoformat(t2_str.replace("Z", "+00:00"))
                        else:
                            t2 = datetime.now(timezone.utc)
                        diff = int((t2 - t1).total_seconds())
                        if diff > 0:
                            duration = f"{diff // 60}m {diff % 60}s"
                except Exception:
                    pass

            run_id = j.get("run_id")
            repo_name = j.get("repo", config.GKI_REPO)
            github_link = (
                f"https://github.com/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}"
                if run_id
                else "#"
            )
            nightly_link = (
                f"https://nightly.link/{config.GITHUB_OWNER}/{repo_name}/actions/runs/{run_id}"
                if run_id
                else "#"
            )

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
                "ksu_mode": effective_mode(inputs) if j.get("type") != "oki" else "",
                "nomount": _enabled(inputs.get("use_nomount")),
                "networking": _enabled(inputs.get("use_networking")),
                "ntsync": _enabled(inputs.get("droidspaces_ntsync")),
                "cve_2026_43499_patch": _enabled(inputs.get("cve_2026_43499_patch")),
                "status": status,
                "duration": _html_text(duration),
                "date": j.get("created_at"),
                "user_name": user_name,
                "github_link": github_link,
                "nightly_link": nightly_link,
            }
            data["builds"].append(b)

        return data
    except Exception as e:
        logger.error("Error in get_realtime_data: %s", e)
        return {"status": "error", "message": str(e), "builds": []}
