"""Permission helpers — tách riêng để tránh circular import."""
import config


def is_owner(user_id: int) -> bool:
    """Owner: quyền cao nhất, duy nhất 1 người."""
    return int(user_id) == int(config.OWNER_ID)


async def is_admin(user_id: int, storage) -> bool:
    """Admin: owner + admin list (từ config + data.json)."""
    if is_owner(user_id):
        return True
    uid = int(user_id)
    if uid in config.ADMIN_IDS:
        return True
    dynamic_admins = await storage.get_admin_ids()
    return uid in dynamic_admins
