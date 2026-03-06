from typing import Dict, Any, List
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, CommandHandler, filters
)
from permissions import is_admin

# States
(
    GKI_KSU_VARIANT,
    GKI_KSU_BRANCH,
    GKI_VERSION,
    # GKI_CUSTOM_NAME,
    # GKI_BUILD_TIME,
    GKI_TOGGLE_ZRAM,
    GKI_TOGGLE_BBG,
    GKI_TOGGLE_KPM,
    GKI_TOGGLE_SUSFS,
    GKI_BUILD_TARGET,
    GKI_SUB_VERSION,
    GKI_RELEASE_TYPE,
    GKI_CONFIRM
) = range(10)

VARIANTS = ["SukiSU", "ReSukiSU", "Official", "Next", "MKSU"]
BRANCHES = ["Stable(标准)", "Dev(开发)"]
RELEASE_TYPES = ["Actions", "Pre-Release", "Release"]
BUILD_TARGETS = [
    ("Android 12 - 5.10", "build_a12_5_10"),
    ("Android 13 - 5.15", "build_a13_5_15"),
    ("Android 14 - 6.1", "build_a14_6_1"),
    ("Android 15 - 6.6", "build_a15_6_6"),
]

# Sub-version (sub_level) lists per build target
SUB_LEVELS = {
    "build_a12_5_10": ["66","81","101","110","117","136","149","160","168","177","185","198","205","209","218","226","233","236","237","240","246"],
    "build_a13_5_15": ["74","78","94","104","119","123","137","144","148","149","151","153","167","170","178","180","185","189","194"],
    "build_a14_6_1": ["25","43","57","68","75","78","84","90","93","99","112","115","118","124","128","129","134","138","141","145","157"],
    "build_a15_6_6": ["50","56","57","58","66","77","82","87","89","92","98","102","118"],
}


def _kb_from_list(prefix: str, values: List[str], back_cb: str = ""):
    rows, row = [], []
    for i, v in enumerate(values, start=1):
        row.append(InlineKeyboardButton(v, callback_data=f"{prefix}:{v}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row:
        rows.append(row)
    if back_cb:
        rows.append([
            InlineKeyboardButton("⬅️", callback_data=back_cb),
            InlineKeyboardButton("❌", callback_data="gki:cancel")
        ])
    else:
        rows.append([InlineKeyboardButton("❌", callback_data="gki:cancel")])
    return InlineKeyboardMarkup(rows)


def _yes_no(prefix: str, recommend: str = "", back_cb: str = ""):
    label_on = "✅ Bật"
    label_off = "❌ Tắt"
    rows = [
        [
            InlineKeyboardButton(label_on, callback_data=f"{prefix}:true"),
            InlineKeyboardButton(label_off, callback_data=f"{prefix}:false"),
        ]
    ]
    if back_cb:
        rows.append([
            InlineKeyboardButton("⬅️", callback_data=back_cb),
            InlineKeyboardButton("❌", callback_data="gki:cancel")
        ])
    else:
        rows.append([InlineKeyboardButton("❌", callback_data="gki:cancel")])
    return InlineKeyboardMarkup(rows)


def _build_target_keyboard(back_cb: str = ""):
    """Keyboard cho phép chọn 1 target duy nhất."""
    rows = []
    for label, key in BUILD_TARGETS:
        rows.append([InlineKeyboardButton(label, callback_data=f"gkitgt:{key}")])
    if back_cb:
        rows.append([
            InlineKeyboardButton("⬅️", callback_data=back_cb),
            InlineKeyboardButton("❌", callback_data="gki:cancel")
        ])
    else:
        rows.append([InlineKeyboardButton("❌", callback_data="gki:cancel")])
    return InlineKeyboardMarkup(rows)


async def _ensure_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    owner = context.chat_data.get("gki_owner")
    
    # Cho phép chính chủ HOẶC admin được thao tác
    if owner is None or owner == uid:
        return True
        
    storage = context.application.bot_data.get("storage")
    if storage and await is_admin(uid, storage):
        return True
        
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    return False


def _task_header(context) -> str:
    owner_name = context.chat_data.get("gki_owner_name", "Unknown")
    owner_id = context.chat_data.get("gki_owner", 0)
    return f'📋 <b>Task by <a href="tg://user?id={owner_id}">{owner_name}</a></b>\n\n'


async def _safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _update_bot_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str,
                          reply_markup=None, parse_mode=None):
    """Edit tin nhắn bot đang track, hoặc gửi mới nếu chưa có."""
    bot_msg_id = context.user_data.get("gki_bot_msg_id")
    if bot_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=bot_msg_id,
                text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
            return
        except Exception:
            pass
    msg = await context.bot.send_message(
        chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode
    )
    context.user_data["gki_bot_msg_id"] = msg.message_id


def _cleanup(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gki", None)
    context.user_data.pop("build_key", None)
    context.user_data.pop("gki_bot_msg_id", None)
    context.chat_data.pop("gki_owner", None)
    context.chat_data.pop("gki_owner_name", None)

async def _del_msg_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try:
        await context.bot.delete_message(chat_id=job.chat_id, message_id=job.data)
    except Exception:
        pass


class GKIFlow:
    def __init__(self, gh, storage, config):
        self.gh = gh
        self.storage = storage
        self.config = config

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip() if update.message else ""
        parts = text.split(maxsplit=1)
        key = parts[1].strip() if len(parts) == 2 else None
        chat_id = update.effective_chat.id
        user_is_admin = await is_admin(update.effective_user.id, self.storage)

        # User thường: check key ngay
        if not user_is_admin:
            if not key:
                m = await update.message.reply_text("⚠️ Thiếu key. Dùng: /gki {key}")
                if context.job_queue:
                    context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
                return ConversationHandler.END
            uses = await self.storage.get_uses(key)
            if uses <= 0:
                m = await update.message.reply_text("⚠️ Key không hợp lệ hoặc đã hết lượt.")
                if context.job_queue:
                    context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
                return ConversationHandler.END
            context.user_data["build_key"] = key

        await _safe_delete(context, chat_id, update.message.message_id)

        context.chat_data["gki_owner"] = update.effective_user.id
        context.chat_data["gki_owner_name"] = update.effective_user.full_name
        context.user_data["gki"] = {"inputs": {
            "kernelsu_variant": "SukiSU",
            "kernelsu_branch": "Stable(标准)",
            "version": "",
            "custom_name": "",
            "build_time": "",
            "use_zram": True,
            "use_bbg": True,
            "use_kpm": True,
            "cancel_susfs": False,
            "build_a12_5_10": False,
            "build_a13_5_15": False,
            "build_a14_6_1": False,
            "build_a15_6_6": False,
            "build_all": False,
            "release_type": "Actions",
            "sub_levels": "",
        }}

        header = _task_header(context)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=header + "Chọn KernelSU variant:",
            reply_markup=_kb_from_list("gkiksuvar", VARIANTS),
            parse_mode="HTML"
        )
        context.user_data["gki_bot_msg_id"] = msg.message_id
        return GKI_KSU_VARIANT

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query and not await _ensure_owner(update, context):
            return ConversationHandler.END
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("❌ Đã huỷ phiên.")
        
        if context.job_queue:
            context.job_queue.run_once(
                _del_msg_job,
                when=60,
                chat_id=q.message.chat_id,
                data=q.message.message_id
            )
            
        _cleanup(context)
        return ConversationHandler.END

    async def timeout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        notice = "⚠️ Phiên hết hạn rồi. Gửi /gki để bắt đầu lại."
        timeout_chat_id = None
        timeout_message_id = None
        q = update.callback_query
        if q:
            try:
                await q.answer()
            except Exception:
                pass
            if q.message:
                timeout_chat_id = q.message.chat_id
                timeout_message_id = q.message.message_id
            try:
                await q.edit_message_text(notice)
            except Exception:
                pass
        elif update.effective_message:
            try:
                m = await update.effective_message.reply_text(notice)
                timeout_chat_id = m.chat_id
                timeout_message_id = m.message_id
            except Exception:
                pass

        if context.job_queue and timeout_chat_id and timeout_message_id:
            context.job_queue.run_once(
                _del_msg_job,
                when=60,
                chat_id=timeout_chat_id,
                data=timeout_message_id
            )

        _cleanup(context)
        return ConversationHandler.END
    async def back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return ConversationHandler.END
        q = update.callback_query
        await q.answer()

        gki_data = context.user_data.get("gki")
        if not gki_data:
            await q.edit_message_text("⚠️ Phiên đã hết. Vui lòng gửi /gki lại.")
            return ConversationHandler.END

        _, target = q.data.split(":", 1)
        header = _task_header(context)

        if target == "ksu_variant":
            await q.edit_message_text(
                header + "Chọn KernelSU variant:",
                reply_markup=_kb_from_list("gkiksuvar", VARIANTS),
                parse_mode="HTML"
            )
            return GKI_KSU_VARIANT

        if target == "ksu_branch":
            await q.edit_message_text(
                header + "Chọn nhánh KernelSU:",
                reply_markup=_kb_from_list("gkiksubr", BRANCHES, back_cb="gkiback:ksu_variant"),
                parse_mode="HTML"
            )
            return GKI_KSU_BRANCH

        if target == "version":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Dùng mặc định", callback_data="gkiver:none")],
                [InlineKeyboardButton("⬅️", callback_data="gkiback:ksu_branch"), InlineKeyboardButton("❌", callback_data="gki:cancel")]
            ])
            await q.edit_message_text(
                header + "⏭️ Nhập <code>tên version</code>.\nVD: JinYan thì sẽ có dạng: 5.10.209-JinYan\nHoặc bấm nút để bỏ qua.",
                reply_markup=kb, parse_mode="HTML"
            )
            return GKI_VERSION

        # if target == "custom_name":
        #     kb = InlineKeyboardMarkup([
        #         [InlineKeyboardButton("⏭️ Dùng mặc định", callback_data="gkicname:none")],
        #         [InlineKeyboardButton("⬅️", callback_data="gkiback:version"), InlineKeyboardButton("❌", callback_data="gki:cancel")]
        #     ])
        #     await q.edit_message_text(
        #         header + "⏭️ Nhập <code>custom_name</code> (hậu tố cho tên file ZIP).\nHoặc bấm nút để dùng mặc định.",
        #         reply_markup=kb, parse_mode="HTML"
        #     )
            # return GKI_CUSTOM_NAME

        if target == "zram":
            await q.edit_message_text(
                header + "Bật ZRAM? (mặc định: bật)",
                reply_markup=_yes_no("gkizr", back_cb="gkiback:custom_name"),
                parse_mode="HTML"
            )
            return GKI_TOGGLE_ZRAM

        if target == "bbg":
            await q.edit_message_text(
                header + "Bật BBG? (mặc định: bật)",
                reply_markup=_yes_no("gkibb", back_cb="gkiback:zram"),
                parse_mode="HTML"
            )
            return GKI_TOGGLE_BBG

        if target == "kpm":
            await q.edit_message_text(
                header + "Bật KPM? (mặc định: bật)",
                reply_markup=_yes_no("gkikpm", back_cb="gkiback:bbg"),
                parse_mode="HTML"
            )
            return GKI_TOGGLE_KPM

        if target == "susfs":
            await q.edit_message_text(
                header + "Tắt SUSFS? (mặc định: không tắt)",
                reply_markup=_yes_no("gkisusfs", back_cb="gkiback:kpm"),
                parse_mode="HTML"
            )
            return GKI_TOGGLE_SUSFS

        if target == "target":
            await q.edit_message_text(
                header + "Chọn phiên bản Android để build:",
                reply_markup=_build_target_keyboard(back_cb="gkiback:susfs"),
                parse_mode="HTML"
            )
            return GKI_BUILD_TARGET

        if target == "sub":
            inputs = context.user_data["gki"]["inputs"]
            target_key = context.user_data["gki"].get("selected_target")
            if not target_key:
                target_key = next((k for _, k in BUILD_TARGETS if inputs.get(k)), BUILD_TARGETS[0][1])
                context.user_data["gki"]["selected_target"] = target_key

            available = SUB_LEVELS.get(target_key, [])
            selected = context.user_data["gki"].get("selected_subs")
            if not isinstance(selected, set):
                subs_raw = (inputs.get("sub_levels") or "").strip()
                if not subs_raw:
                    selected = set(available)
                else:
                    selected = {sv for sv in [x.strip() for x in subs_raw.split(",")] if sv in available}
                context.user_data["gki"]["selected_subs"] = selected

            target_label = next((label for label, k in BUILD_TARGETS if k == target_key), target_key)
            count = len(selected)
            total = len(available)
            user_is_admin = await is_admin(update.effective_user.id, self.storage)
            kb = self._sub_version_keyboard(context, user_is_admin)
            await q.edit_message_text(
                header + f"Chọn sub-version cho <b>{target_label}</b>:\n"
                         f"<i>(Đã chọn: {count}/{total})</i>",
                reply_markup=kb, parse_mode="HTML"
            )
            return GKI_SUB_VERSION

        if target == "release":
            await q.edit_message_text(
                header + "Chọn loại release: (Nên chọn Actions)",
                reply_markup=_kb_from_list("gkirel", RELEASE_TYPES, back_cb="gkiback:sub"),
                parse_mode="HTML"
            )
            return GKI_RELEASE_TYPE

        await q.answer("Không thể quay lại bước này.", show_alert=True)
        return ConversationHandler.END
    # === VARIANT ===
    async def set_variant(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_KSU_VARIANT
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["kernelsu_variant"] = val
        header = _task_header(context)
        await q.edit_message_text(header + "Chọn nhánh KernelSU:", reply_markup=_kb_from_list("gkiksubr", BRANCHES, back_cb="gkiback:ksu_variant"), parse_mode="HTML")
        return GKI_KSU_BRANCH

    # === BRANCH ===
    async def set_branch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_KSU_BRANCH
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["kernelsu_branch"] = val
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Dùng mặc định", callback_data="gkiver:none")],
            [InlineKeyboardButton("⬅️", callback_data="gkiback:ksu_branch"), InlineKeyboardButton("❌", callback_data="gki:cancel")]
        ])
        header = _task_header(context)
        await q.edit_message_text(
            header + "⏭️ Nhập <code>tên version</code>.\nVD: JinYan thì sẽ có dạng: 5.10.209-JinYan\nHoặc bấm nút để bỏ qua.",
            reply_markup=kb, parse_mode="HTML"
        )
        return GKI_VERSION

    # === VERSION (text input or button) ===
    async def set_version(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if update.callback_query:
            if not await _ensure_owner(update, context): return GKI_VERSION
            q = update.callback_query; await q.answer()
            context.user_data["gki"]["inputs"]["version"] = ""
        else:
            txt = (update.message.text or "").strip()
            if txt.lower() == "none":
                val = ""
            else:
                val = txt if txt.startswith("-") else f"-{txt}"
            context.user_data["gki"]["inputs"]["version"] = val
            await _safe_delete(context, chat_id, update.message.message_id)
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ Dùng mặc định", callback_data="gkicname:none")],
            [InlineKeyboardButton("⬅️", callback_data="gkiback:version"), InlineKeyboardButton("❌", callback_data="gki:cancel")]
        ])
        header = _task_header(context)
        await _update_bot_msg(context, chat_id,
            header + "⏭️ Nhập <code>custom_name</code> (hậu tố cho tên file ZIP).\nHoặc bấm nút để dùng mặc định.",
            reply_markup=kb, parse_mode="HTML")
        # return GKI_CUSTOM_NAME
        
        # --- SKIP CUSTOM_NAME AND BUILD_TIME ---
        context.user_data["gki"]["inputs"]["custom_name"] = ""
        context.user_data["gki"]["inputs"]["build_time"] = ""
        header = _task_header(context)
        await _update_bot_msg(context, chat_id, header + "Bật ZRAM? (mặc định: bật)", reply_markup=_yes_no("gkizr", back_cb="gkiback:version"), parse_mode="HTML")
        return GKI_TOGGLE_ZRAM

    # === CUSTOM NAME ===
    # async def set_custom_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    #     if not await _ensure_owner(update, context): return GKI_CUSTOM_NAME
    #     chat_id = update.effective_chat.id
    #     if update.callback_query:
    #         q = update.callback_query; await q.answer()
    #         context.user_data["gki"]["inputs"]["custom_name"] = ""
    #     else:
    #         txt = (update.message.text or "").strip()
    #         context.user_data["gki"]["inputs"]["custom_name"] = "" if txt.lower() == "none" else txt
    #         await _safe_delete(context, chat_id, update.message.message_id)

    #     # Skip build time override for everyone and auto-set to None
    #     context.user_data["gki"]["inputs"]["build_time"] = ""
    #     header = _task_header(context)
    #     await _update_bot_msg(context, chat_id, header + "Bật ZRAM? (mặc định: bật)", reply_markup=_yes_no("gkizr", back_cb="gkiback:custom_name"), parse_mode="HTML")
    #     # return GKI_TOGGLE_ZRAM

    # === TOGGLES ===
    async def toggle_zram(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_TOGGLE_ZRAM
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["use_zram"] = (val == "true")
        header = _task_header(context)
        await q.edit_message_text(header + "Bật BBG? (mặc định: bật)", reply_markup=_yes_no("gkibb", back_cb="gkiback:zram"), parse_mode="HTML")
        return GKI_TOGGLE_BBG

    async def toggle_bbg(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_TOGGLE_BBG
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["use_bbg"] = (val == "true")
        header = _task_header(context)
        await q.edit_message_text(header + "Bật KPM? (mặc định: bật)", reply_markup=_yes_no("gkikpm", back_cb="gkiback:bbg"), parse_mode="HTML")
        return GKI_TOGGLE_KPM

    async def toggle_kpm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_TOGGLE_KPM
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["use_kpm"] = (val == "true")
        header = _task_header(context)
        await q.edit_message_text(header + "Tắt SUSFS? (mặc định: không tắt)", reply_markup=_yes_no("gkisusfs", back_cb="gkiback:kpm"), parse_mode="HTML")
        return GKI_TOGGLE_SUSFS

    async def toggle_susfs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_TOGGLE_SUSFS
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["cancel_susfs"] = (val == "true")
        # Hiển thị chọn build target (chỉ được chọn 1)
        header = _task_header(context)
        await q.edit_message_text(header + "Chọn phiên bản Android để build:",
                                  reply_markup=_build_target_keyboard(back_cb="gkiback:susfs"), parse_mode="HTML")
        return GKI_BUILD_TARGET

    # === BUILD TARGET (single-select) ===
    async def set_build_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_BUILD_TARGET
        q = update.callback_query; await q.answer()
        _, key = q.data.split(":", 1)

        inputs = context.user_data["gki"]["inputs"]
        # Tắt hết rồi chỉ bật cái được chọn
        for _, k in BUILD_TARGETS:
            inputs[k] = (k == key)

        # Lưu target key và khởi tạo selected sub-versions = all
        context.user_data["gki"]["selected_target"] = key
        available = SUB_LEVELS.get(key, [])
        context.user_data["gki"]["selected_subs"] = set()  # mặc định không chọn gì

        user_is_admin = await is_admin(update.effective_user.id, self.storage)

        # Hiển thị chọn sub-version
        header = _task_header(context)
        target_label = next((label for label, k in BUILD_TARGETS if k == key), key)
        kb = self._sub_version_keyboard(context, user_is_admin)
        
        info_text = "<i>(Bấm để chọn, sẽ tự động chuyển tiếp)</i>" if not user_is_admin else "<i>(Bấm để bật/tắt, ✅ = sẽ build)</i>"
        
        await q.edit_message_text(
            header + f"Chọn sub-version cho <b>{target_label}</b>:\n{info_text}",
            reply_markup=kb, parse_mode="HTML")
        return GKI_SUB_VERSION

    # === SUB-VERSION KEYBOARD BUILDER ===
    def _sub_version_keyboard(self, context, user_is_admin: bool):
        target_key = context.user_data["gki"]["selected_target"]
        available = SUB_LEVELS.get(target_key, [])
        selected = context.user_data["gki"]["selected_subs"]
        all_selected = len(selected) == len(available)

        rows = []
        # Build All toggle
        if user_is_admin:
            all_icon = "✅" if all_selected else "⬜"
            rows.append([InlineKeyboardButton(f"{all_icon} Build All", callback_data="gkisub:all")])

        # Sub-version buttons (4 per row)
        row = []
        target_label = next((label for label, k in BUILD_TARGETS if k == target_key), target_key)
        major = target_label.split(" - ")[-1] if " - " in target_label else ""
        for sv in available:
            icon = "✅ " if sv in selected else "⬜ " if user_is_admin else ""
            btn_text = f"{icon}{major}.{sv}"
            row.append(InlineKeyboardButton(btn_text, callback_data=f"gkisub:{sv}"))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        # Confirm + Back/Cancel
        if user_is_admin:
            rows.append([InlineKeyboardButton("➡️ Tiếp tục", callback_data="gkisub:done")])
            
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="gkiback:target"),
            InlineKeyboardButton("❌", callback_data="gki:cancel")
        ])
        return InlineKeyboardMarkup(rows)

    # === SUB-VERSION TOGGLE ===
    async def toggle_sub_version(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_SUB_VERSION
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        
        user_is_admin = await is_admin(update.effective_user.id, self.storage)

        target_key = context.user_data["gki"]["selected_target"]
        available = SUB_LEVELS.get(target_key, [])
        selected = context.user_data["gki"]["selected_subs"]

        if not user_is_admin and val not in ("done", "all"):
            # Đối với user thường, chỉ bấm 1 phát vào sub-version là đi thẳng tới Confirm và set Action
            context.user_data["gki"]["selected_subs"] = {val}
            context.user_data["gki"]["inputs"]["sub_levels"] = str(val)
            context.user_data["gki"]["inputs"]["release_type"] = "Actions"
            return await self.confirm(q, context)

        if val == "done":
            # Validate at least 1 selected
            if not selected:
                await q.answer("⚠️ Chọn ít nhất 1 sub-version!", show_alert=True)
                return GKI_SUB_VERSION
            # Save sub_levels to inputs
            if len(selected) == len(available):
                context.user_data["gki"]["inputs"]["sub_levels"] = ""  # empty = all
            else:
                context.user_data["gki"]["inputs"]["sub_levels"] = ",".join(sorted(selected, key=lambda x: int(x)))
            # Move to release type
            header = _task_header(context)
            await q.edit_message_text(header + "Chọn loại release: (Nên chọn Actions)", reply_markup=_kb_from_list("gkirel", RELEASE_TYPES, back_cb="gkiback:sub"), parse_mode="HTML")
            return GKI_RELEASE_TYPE

        if val == "all":
            # Toggle all
            if len(selected) == len(available):
                selected.clear()
            else:
                selected.update(available)
        else:
            # Toggle single
            if val in selected:
                selected.discard(val)
            else:
                selected.add(val)

        context.user_data["gki"]["selected_subs"] = selected
        # Update keyboard
        header = _task_header(context)
        target_label = next((label for label, k in BUILD_TARGETS if k == target_key), target_key)
        count = len(selected)
        total = len(available)
        kb = self._sub_version_keyboard(context, user_is_admin)
        await q.edit_message_text(
            header + f"Chọn sub-version cho <b>{target_label}</b>:\n"
                     f"<i>(Đã chọn: {count}/{total})</i>",
            reply_markup=kb, parse_mode="HTML")
        return GKI_SUB_VERSION

    # === RELEASE TYPE ===
    async def set_release_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_RELEASE_TYPE
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["gki"]["inputs"]["release_type"] = val
        return await self.confirm(q, context)

    # === CONFIRM ===
    async def confirm(self, q, context):
        inputs = context.user_data["gki"]["inputs"]
        pretty = "\n".join([f"• {k}: {v}" for k, v in inputs.items()])
        user_is_admin = await is_admin(q.from_user.id, self.storage)
        back_data = "gkiback:release" if user_is_admin else "gkiback:target"
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Xác nhận", callback_data="gkiconfirm")],
            [InlineKeyboardButton("⬅️", callback_data=back_data), InlineKeyboardButton("❌", callback_data="gki:cancel")]
        ])
        header = _task_header(context)
        await q.edit_message_text(
            header + f"<b>Xác nhận build GKI</b>\n<pre>{pretty}</pre>",
            reply_markup=kb, parse_mode="HTML"
        )
        return GKI_CONFIRM

    # === DISPATCH ===
    async def do_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_CONFIRM
        q = update.callback_query; await q.answer()
        inputs = context.user_data["gki"]["inputs"].copy()
        user = update.effective_user
        key = context.user_data.get("build_key")
        user_is_admin = await is_admin(user.id, self.storage)

        if not user_is_admin:
            uses = await self.storage.get_uses(key or "")
            if not key or uses <= 0:
                m = await q.edit_message_text("❌ Key không hợp lệ hoặc hết lượt.")
                if context.job_queue:
                    context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
                _cleanup(context)
                return ConversationHandler.END

        # Lấy workflow file từ config
        wf_files = list(self.config.GKI_WORKFLOWS.values())
        wf = wf_files[0] if wf_files else "main.yml"

        # Kiểm tra concurrency (chống đè build dẫn tới bị hủy)
        await q.edit_message_text("⏳ Đang kiểm tra trạng thái server...")
        active_runs_count = 0
        busy_run = None
        for status in ["in_progress", "queued"]:
            url = f"{self.gh.base}/repos/{self.config.GITHUB_OWNER}/{self.config.GKI_REPO}/actions/runs?status={status}&per_page=20"
            check_res = await self.gh._request("GET", url)
            if check_res.get("status") == 200:
                runs = check_res["json"].get("workflow_runs", [])
                for r in runs:
                    if r.get("head_branch") == self.config.GKI_DEFAULT_BRANCH and wf in r.get("path", ""):
                        active_runs_count += 1
                        if not busy_run:
                            busy_run = r

        # Giới hạn toàn bộ server tối đa 10 job chạy cùng lúc
        max_concurrent_jobs = 10
        is_busy = active_runs_count >= max_concurrent_jobs
                
        if is_busy:
            # Tính thời gian ước tính còn lại (giả sử tổng ~45 phút) cho run cũ nhất
            eta_line = ""
            if busy_run and busy_run.get("created_at"):
                try:
                    created_dt = datetime.fromisoformat(busy_run["created_at"].replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - created_dt).total_seconds()
                    remaining = max(0, 2700 - elapsed)  # 2700s = 45 phút
                    rem_m = int(remaining // 60)
                    if rem_m > 0:
                        eta_line = f"• Ước tính hoàn tất tiến trình cũ nhất sau: ~{rem_m} phút.\n"
                    else:
                        eta_line = "• Ước tính sắp hoàn tất.\n"
                except Exception:
                    pass
            msg = (
                "❌ <b>Máy chủ đang quá tải!</b>\n\n"
                f"• Hiện tại đang có {active_runs_count} tiến trình đang chạy.\n"
                "• Vui lòng chờ các tiến trình trước hoàn tất rồi thử lại.\n"
                f"{eta_line}\n"
                "<i>Bot sẽ thông báo ngay cho bạn khi có slot trống!</i>"
            )
            await self.storage.add_waiter(user.id, q.message.chat_id, user.full_name)
            await q.edit_message_text(msg, parse_mode="HTML")
            if context.job_queue:
                context.job_queue.run_once(
                    _del_msg_job, when=60,
                    chat_id=q.message.chat_id, data=q.message.message_id
                )
            _cleanup(context)
            return ConversationHandler.END

        res = await self.gh.dispatch_workflow(
            repo=self.config.GKI_REPO,
            workflow_file=wf,
            ref=self.config.GKI_DEFAULT_BRANCH,
            inputs=inputs
        )
        if res["status"] in (201, 202, 204):
            job = {
                "type": "gki",
                "repo": self.config.GKI_REPO,
                "workflow_file": wf,
                "ref": self.config.GKI_DEFAULT_BRANCH,
                "inputs": inputs,
                "user_id": user.id,
                "user_name": user.full_name,
                "chat_id": update.effective_chat.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "run_id": None,
                "status": "dispatched",
                "conclusion": None,
                "notified": False
            }
            await self.storage.add_job(job)
            if not user_is_admin:
                await self.storage.consume(key)
            view_url = f"https://github.com/{self.config.GITHUB_OWNER}/{self.config.GKI_REPO}/actions/workflows/{wf}"
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Mở GitHub Actions", url=view_url)]])
            
            mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
            
            target_count = sum(1 for k in ('build_a12_5_10', 'build_a13_5_15', 'build_a14_6_1', 'build_a15_6_6') if inputs.get(k))
            if inputs.get("build_all"):
                target_count = 4
            target_count = max(1, target_count)
            
            if target_count == 1:
                est_time = "~15-20 phút"
            elif target_count == 2:
                est_time = "~25-30 phút"
            elif target_count == 3:
                est_time = "~35-45 phút"
            else:
                est_time = "~45-60 phút"

            msg_text = (
                f"✅ <b>Đã gửi build thành công!</b>\n"
                f"👤 Người gửi: {mention}\n"
                f"⏱️ Dự tính hoàn thành: {est_time}\n\n"
                f"<i>Bot sẽ tự động gửi file qua tin nhắn khi hoàn tất.</i>"
            )
            await q.edit_message_text(msg_text, reply_markup=btn, parse_mode="HTML")
        else:
            m = await q.edit_message_text(f"⚠️ Dispatch lỗi: {res['status']} {res.get('json')}")
            if context.job_queue:
                context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        _cleanup(context)
        return ConversationHandler.END


def build_gki_conversation(gh, storage, config):
    flow = GKIFlow(gh, storage, config)
    cancel_handler = CallbackQueryHandler(flow.cancel, pattern=r"^gki:cancel$")
    back_handler = CallbackQueryHandler(flow.back, pattern=r"^gkiback:.+$")
    return ConversationHandler(
        entry_points=[CommandHandler("gki", flow.start)],
        states={
            GKI_KSU_VARIANT: [CallbackQueryHandler(flow.set_variant, pattern=r"^gkiksuvar:.+"), back_handler, cancel_handler],
            GKI_KSU_BRANCH: [CallbackQueryHandler(flow.set_branch, pattern=r"^gkiksubr:.+"), back_handler, cancel_handler],
            GKI_VERSION: [
                CallbackQueryHandler(flow.set_version, pattern=r"^gkiver:none$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, flow.set_version),
                back_handler,
                cancel_handler
            ],
            # GKI_CUSTOM_NAME: [
            #     CallbackQueryHandler(flow.set_custom_name, pattern=r"^gkicname:none$"),
            #     MessageHandler(filters.TEXT & ~filters.COMMAND, flow.set_custom_name),
            #     back_handler,
            #     cancel_handler
            # ],
            GKI_TOGGLE_ZRAM: [CallbackQueryHandler(flow.toggle_zram, pattern=r"^gkizr:(true|false)$"), back_handler, cancel_handler],
            GKI_TOGGLE_BBG: [CallbackQueryHandler(flow.toggle_bbg, pattern=r"^gkibb:(true|false)$"), back_handler, cancel_handler],
            GKI_TOGGLE_KPM: [CallbackQueryHandler(flow.toggle_kpm, pattern=r"^gkikpm:(true|false)$"), back_handler, cancel_handler],
            GKI_TOGGLE_SUSFS: [CallbackQueryHandler(flow.toggle_susfs, pattern=r"^gkisusfs:(true|false)$"), back_handler, cancel_handler],
            GKI_BUILD_TARGET: [CallbackQueryHandler(flow.set_build_target, pattern=r"^gkitgt:.+"), back_handler, cancel_handler],
            GKI_SUB_VERSION: [CallbackQueryHandler(flow.toggle_sub_version, pattern=r"^gkisub:.+"), back_handler, cancel_handler],
            GKI_RELEASE_TYPE: [CallbackQueryHandler(flow.set_release_type, pattern=r"^gkirel:.+"), back_handler, cancel_handler],
            GKI_CONFIRM: [CallbackQueryHandler(flow.do_dispatch, pattern=r"^gkiconfirm$"), back_handler, cancel_handler],
            ConversationHandler.TIMEOUT: [
                CallbackQueryHandler(flow.timeout),
                MessageHandler(filters.ALL, flow.timeout),
            ],
        },
        fallbacks=[cancel_handler],
        allow_reentry=True,
        name="gki_conversation",
        persistent=False,
    )
