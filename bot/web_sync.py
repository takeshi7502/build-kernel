import json
import base64
import logging
from datetime import datetime, timezone
import config

logger = logging.getLogger("gww-web-sync")

async def sync_web_data(app):
    """Đổ dữ liệu từ Storage ra file JSON và push lên nhánh web-data trên Github"""
    try:
        storage = app.bot_data.get("storage")
        gh = app.bot_data.get("gh")
        if not storage or not gh:
            return

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
                "user": j.get("user_name", "Unknown"),
                "dl_link": None
            }
            if status == "success" and j.get("run_id"):
                b["dl_link"] = f"https://github.com/{config.GITHUB_OWNER}/{config.GKI_REPO}/actions/runs/{j.get('run_id')}"
                
            data["builds"].append(b)
            
        content_str = json.dumps(data, indent=2)
        
        # 1. Kiểm tra nhánh web-data
        branch_name = "web-data"
        res_branch = await gh._request("GET", f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/branches/{branch_name}")
        if res_branch["status"] != 200:
            # Lấy sha của nhánh chính để tạo
            res_main = await gh._request("GET", f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/git/refs/heads/{config.GKI_DEFAULT_BRANCH}")
            if res_main["status"] == 200:
                main_sha = res_main["json"]["object"]["sha"]
                await gh._request("POST", f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/git/refs", json_payload={
                    "ref": f"refs/heads/{branch_name}",
                    "sha": main_sha
                })
        
        # 2. Lấy SHA của file web_data.json hiện tại
        file_path = "web_data.json"
        res_file = await gh._request("GET", f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/contents/{file_path}?ref={branch_name}")
        file_sha = res_file["json"].get("sha") if res_file["status"] == 200 else None
        
        # 3. Push file mới
        payload = {
            "message": "Update web dashboard data [skip ci]",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
            "branch": branch_name
        }
        if file_sha:
            payload["sha"] = file_sha
            
        await gh._request("PUT", f"{gh.base}/repos/{config.GITHUB_OWNER}/{config.GKI_REPO}/contents/{file_path}", json_payload=payload)
        logger.info("Successfully synced web_data.json to branch web-data")

    except Exception as e:
        logger.error("Error in sync_web_data: %s", e)
