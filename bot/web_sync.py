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
                if inputs.get("build_a12_5_10"): os_list.append("Android 12 (5.10)")
                if inputs.get("build_a13_5_15"): os_list.append("Android 13 (5.15)")
                if inputs.get("build_a14_6_1"): os_list.append("Android 14 (6.1)")
                if inputs.get("build_a15_6_6"): os_list.append("Android 15 (6.6)")
            if not os_list:
                os_list = [j.get("workflow_file", "Custom")]
            
            ksu = f"{inputs.get('kernelsu_variant', 'NoKSU')} {inputs.get('version', '')}".strip()
            susfs = "Tắt (Cancelled)" if inputs.get("cancel_susfs", True) else "Bật (Enabled)"
            
            status = "building"
            if j.get("status") == "completed":
                status = "success" if j.get("conclusion") == "success" else "failed"
                
            b = {
                "id": str(j.get("run_id") or j.get("_id", "TBD")),
                "os_version": " / ".join(os_list),
                "ksu_version": ksu,
                "susfs_version": susfs,
                "status": status,
                "date": j.get("created_at"),
                "commit": inputs.get("sub_levels", "N/A"),
                "commit_msg": j.get("commit_msg", ""),
                "artifacts_url": j.get("telegraph_url"),
                "build_log": j.get("build_log", ""),
                "user": j.get("user_name", "Unknown"),
                "dl_link": None
            }
            if status == "success" and j.get("run_id"):
                b["dl_link"] = f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{j.get('run_id')}"
                
            data["builds"].append(b)
            
        return data
    except Exception as e:
        logger.error("Error in get_realtime_data: %s", e)
        return {"status": "error", "message": str(e), "builds": []}
