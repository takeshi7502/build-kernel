import aiohttp
import logging

logger = logging.getLogger("notify")

async def send_admin_notification(bot_token: str, owner_id: int, mention: str, view_url: str, job_type: str = ""):
    """Gửi thông báo có build mới tới Admin qua Telegram Bot API."""
    job_prefix = f" {job_type}" if job_type else ""
    admin_msg = (
        f"🚀 <b>Có build{job_prefix} mới từ {mention}!</b>\n"
        f"<blockquote><b>Xem : <a href='{view_url}'>Github</a> | <a href='https://kernel.takeshi.dev/'>Dashboard</a></b></blockquote>"
    )
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": owner_id, 
        "text": admin_msg, 
        "parse_mode": "HTML", 
        "disable_web_page_preview": True
    }
    
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=payload):
                pass
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)
