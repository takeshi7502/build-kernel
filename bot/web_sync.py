import json
import base64
import logging
from datetime import datetime, timezone
import config

logger = logging.getLogger("gww-web-sync")

async def get_realtime_data(app):
    """Trích xuất dữ liệu Real-time trực tiếp từ File và Github API qua RAM (0 delay)"""
    try:
        storage = app.bot_data.get("storage")
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
        
        for j in jobs[:50]: # Lấy 50 build gần nhất
            inputs = j.get("inputs", {})
            
            os_list = []
            if inputs.get("build_all"):
                os_list = ["ALL versions"]
            else:
                if inputs.get("build_a12_5_10"): os_list.append("A12-5.10")
                if inputs.get("build_a13_5_15"): os_list.append("A13-5.15")
                if inputs.get("build_a14_6_1"): os_list.append("A14-6.1")
                if inputs.get("build_a15_6_6"): os_list.append("A15-6.6")
            
            os_str = os_list[0] if os_list else "Custom"
            sub = inputs.get("sub_levels", "").replace(",", ".")
            if sub and sub.lower() not in ["all", "*"]:
                os_str = f"{os_str}.{sub}"
                
            variant = inputs.get("kernelsu_variant", "NoKSU")
            branch = str(inputs.get("kernelsu_branch", "Stable")).replace("(标准)", "").replace("(开发)", "").strip()
            if not branch: branch = "Stable"
                
            title = variant
            sub_title = f"({os_str}-{branch})"
            
            custom_version = inputs.get("version", "").strip("-") or "Takeshi.dev"
            zram = "Bật" if inputs.get("use_zram", True) else "Tắt"
            bbg = "Bật" if inputs.get("use_bbg", True) else "Tắt"
            kpm = "Bật" if inputs.get("use_kpm", True) else "Tắt"
            susfs = "Tắt" if inputs.get("cancel_susfs", True) else "Bật"
            
            status = "building"
            if j.get("status") == "completed":
                status = "success" if j.get("conclusion") == "success" else "failed"
                
            run_id = j.get("run_id")
            github_link = f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}" if run_id else "#"
            nightly_link = f"https://nightly.link/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{run_id}" if run_id else "#"
            
            b = {
                "id": str(run_id or j.get("_id", "TBD")),
                "title": title,
                "sub_title": sub_title,
                "custom_version": custom_version,
                "zram": zram,
                "kpm": kpm,
                "bbg": bbg,
                "susfs": susfs,
                "status": status,
                "date": j.get("created_at"),
                "github_link": github_link,
                "nightly_link": nightly_link
            }
            data["builds"].append(b)
            
        return data
    except Exception as e:
        logger.error("Error in get_realtime_data: %s", e)
        return {"status": "error", "message": str(e), "builds": []}
