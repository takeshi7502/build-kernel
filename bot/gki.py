from typing import Dict, Any, List
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, CommandHandler, filters
)
from permissions import is_admin
from config import send_admin_notification

# States
(
    GKI_KSU_VARIANT,
    GKI_KSU_BRANCH,
    GKI_VERSION,
    GKI_TOGGLES,
    GKI_BUILD_TARGET,
    GKI_SUB_VERSION,
    GKI_RELEASE_TYPE,
    GKI_CONFIRM
) = range(8)

VARIANTS = ["SukiSU", "ReSukiSU", "Official", "Next", "MKSU"]
BRANCHES = ["Stable(标准)", "Dev(开发)"]
RELEASE_TYPES = ["Actions", "Pre-Release", "Release"]
BUILD_TARGETS = [
    ("A12 - 5.10", "build_a12_5_10"),
    ("A13 - 5.15", "build_a13_5_15"),
    ("A14 - 6.1", "build_a14_6_1"),
    ("A15 - 6.6", "build_a15_6_6"),
    ("A16 - 6.12", "build_a16_6_12"),
]

# Sub-version (sub_level) lists per build target
SUB_LEVELS = {
    "build_a12_5_10": ["66","81","101","110","117","136","149","160","168","177","185","198","205","209","218","226","233","236","237","240","246","X"],
    "build_a13_5_15": ["74","78","94","104","119","123","137","144","148","149","151","153","167","170","178","180","185","189","194","X"],
    "build_a14_6_1": ["25","43","57","68","75","78","84","90","93","99","112","115","118","124","128","129","134","138","141","145","157","X"],
    "build_a15_6_6": ["50","56","57","58","66","77","82","87","89","92","98","102","118","X"],
    "build_a16_6_12": ["23","30","38","58","X"],
}

# Metadata per sub_level → (os_patch_level, revision)
SUB_LEVEL_META: Dict[str, Dict[str, tuple]] = {
    "build_a12_5_10": {
        "66":("2022-01","r11"),"81":("2022-03","r11"),"101":("2022-04","r28"),
        "110":("2022-07","r1"),"117":("2022-09","r1"),"136":("2022-11","r15"),
        "149":("2023-01","r1"),"160":("2023-03","r1"),"168":("2023-04","r9"),
        "177":("2023-07","r3"),"185":("2023-09","r1"),"198":("2024-01","r17"),
        "205":("2024-03","r1"),"209":("2024-05","r13"),"218":("2024-08","r14"),
        "226":("2024-11","r8"),"233":("2025-02","r1"),"236":("2025-05","r1"),
        "237":("2025-06","r1"),"240":("2025-09","r1"),"246":("2025-12","r1"),
        "X":("lts","r1"),
    },
    "build_a13_5_15": {
        "74":("2023-01",""),"78":("2023-03",""),"94":("2023-05",""),
        "104":("2023-07",""),"119":("2023-09",""),"123":("2023-11",""),
        "137":("2024-01",""),"144":("2024-03",""),"148":("2024-05",""),
        "149":("2024-07",""),"151":("2024-08",""),"153":("2024-09",""),
        "167":("2024-11",""),"170":("2025-01",""),"178":("2025-03",""),
        "180":("2025-05",""),"185":("2025-07",""),"189":("2025-09",""),
        "194":("2025-12",""),"X":("lts",""),
    },
    "build_a14_6_1": {
        "25":("2023-10",""),"43":("2023-11",""),"57":("2024-01",""),
        "68":("2024-03",""),"75":("2024-05",""),"78":("2024-06",""),
        "84":("2024-07",""),"90":("2024-08",""),"93":("2024-09",""),
        "99":("2024-10",""),"112":("2024-11",""),"115":("2024-12",""),
        "118":("2025-01",""),"124":("2025-02",""),"128":("2025-03",""),
        "129":("2025-04",""),"134":("2025-05",""),"138":("2025-06",""),
        "141":("2025-07",""),"145":("2025-09",""),"157":("2025-12",""),
        "X":("lts",""),
    },
    "build_a15_6_6": {
        "50":("2024-06",""),"56":("2024-09",""),"57":("2024-10",""),
        "58":("2024-11",""),"66":("2025-01",""),"77":("2025-03",""),
        "82":("2025-04",""),"87":("2025-05",""),"89":("2025-06",""),
        "92":("2025-07",""),"98":("2025-09",""),"102":("2025-10",""),
        "118":("2026-01",""),"X":("lts",""),
    },
    "build_a16_6_12": {
        "23":("2025-06",""),"30":("2025-07",""),"38":("2025-09",""),
        "58":("2025-12",""),"X":("lts",""),
    },
}

TARGET_META: Dict[str, tuple] = {
    "build_a12_5_10": ("android12", "5.10"),
    "build_a13_5_15": ("android13", "5.15"),
    "build_a14_6_1":  ("android14", "6.1"),
    "build_a15_6_6":  ("android15", "6.6"),
    "build_a16_6_12": ("android16", "6.12"),
}

# Targets hỗ trợ supp_op (OnePlus 8E)
SUPP_OP_TARGETS = {"build_a15_6_6", "build_a16_6_12"}

CUSTOM_WORKFLOW = "kernel-custom.yml"


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
    """Keyboard chọn 1 target, xếp 2 cột 2 hàng."""
    rows = []
    targets = [(label, key) for label, key in BUILD_TARGETS]
    for i in range(0, len(targets), 2):
        row = []
        for label, key in targets[i:i+2]:
            row.append(InlineKeyboardButton(label, callback_data=f"gkitgt:{key}"))
        rows.append(row)
    if back_cb:
        rows.append([
            InlineKeyboardButton("⬅️", callback_data=back_cb),
            InlineKeyboardButton("❌", callback_data="gki:cancel")
        ])
    else:
        rows.append([InlineKeyboardButton("❌", callback_data="gki:cancel")])
    return InlineKeyboardMarkup(rows)


TOGGLE_FEATURES = [
    ("ZRAM",  "use_zram",       "gkitog:zram"),
    ("BBG",   "use_bbg",        "gkitog:bbg"),
    ("KPM",   "use_kpm",        "gkitog:kpm"),
    ("SUSFS", "cancel_susfs",   "gkitog:susfs"),
    ("Support 1+ 8E", "supp_op",        "gkitog:supp_op"),
]

def _toggles_keyboard(inputs: dict, back_cb: str = "", selected_target: str = "") -> InlineKeyboardMarkup:
    """Nút bật/tắt features, ẩn OP 8E nếu target không hỗ trợ."""
    rows = []
    btns = []
    for label, key, cb in TOGGLE_FEATURES:
        # Ẩn supp_op nếu target không phải A15/A16
        if key == "supp_op" and selected_target not in SUPP_OP_TARGETS:
            continue
        # SUSFS: cancel_susfs=True nghĩa là tắt SUSFS
        if key == "cancel_susfs":
            active = not inputs.get(key, True)
        else:
            active = inputs.get(key, False)
        icon = "✅" if active else "⬜"
        btns.append(InlineKeyboardButton(f"{icon} {label}", callback_data=cb))
    # Xếp 2 cột
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])
    nav = []
    if back_cb:
        nav.append(InlineKeyboardButton("⬅️", callback_data=back_cb))
    nav.append(InlineKeyboardButton("❌", callback_data="gki:cancel"))
    nav.append(InlineKeyboardButton("➡️", callback_data="gkitog:next"))
    rows.append(nav)
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
            "use_zram": False,
            "use_bbg": False,
            "use_kpm": False,
            "cancel_susfs": True,   # True = tắt SUSFS (mặc định SUSFS tắt)
            "supp_op": False,
            "build_a12_5_10": False,
            "build_a13_5_15": False,
            "build_a14_6_1": False,
            "build_a15_6_6": False,
            "build_a16_6_12": False,
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

        if target == "target":
            await q.edit_message_text(
                header + "Chọn phiên bản Android để build:",
                reply_markup=_build_target_keyboard(back_cb="gkiback:version"),
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

        if target == "toggles":
            inputs = context.user_data["gki"]["inputs"]
            selected_target = context.user_data["gki"].get("selected_target", "")
            await q.edit_message_text(
                header + "<b>Tùy chỉnh tính năng:</b>",
                reply_markup=_toggles_keyboard(inputs, back_cb="gkiback:sub", selected_target=selected_target),
                parse_mode="HTML"
            )
            return GKI_TOGGLES

        if target == "release":
            await q.edit_message_text(
                header + "Chọn loại release: (Nên chọn Actions)",
                reply_markup=_kb_from_list("gkirel", RELEASE_TYPES, back_cb="gkiback:toggles"),
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

        header = _task_header(context)
        await _update_bot_msg(context, chat_id,
            header + "Chọn phiên bản Android để build:",
            reply_markup=_build_target_keyboard(back_cb="gkiback:version"),
            parse_mode="HTML")
        return GKI_BUILD_TARGET

    # === TOGGLES COMBINED ===
    async def toggle_feature(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return GKI_TOGGLES
        q = update.callback_query; await q.answer()
        _, key = q.data.split(":", 1)
        inputs = context.user_data["gki"]["inputs"]

        if key == "next":
            # Admin → chọn release type; User → confirm thẳng
            header = _task_header(context)
            user_is_admin = await is_admin(update.effective_user.id, self.storage)
            if user_is_admin:
                await q.edit_message_text(
                    header + "Chọn loại release: (Nên chọn Actions)",
                    reply_markup=_kb_from_list("gkirel", RELEASE_TYPES, back_cb="gkiback:toggles"),
                    parse_mode="HTML")
                return GKI_RELEASE_TYPE
            else:
                # User: mặc định Actions, chuyển thẳng sang confirm
                context.user_data["gki"]["inputs"]["release_type"] = "Actions"
                return await self.confirm(q, context)

        # Toggle feature
        toggle_map = {
            "zram":    "use_zram",
            "bbg":     "use_bbg",
            "kpm":     "use_kpm",
            "susfs":   "cancel_susfs",
            "supp_op": "supp_op",
        }
        input_key = toggle_map.get(key)
        if input_key:
            inputs[input_key] = not inputs.get(input_key, False)

        header = _task_header(context)
        selected_target = context.user_data["gki"].get("selected_target", "")
        await q.edit_message_text(
            header + "<b>Tùy chỉnh tính năng:</b>",
            reply_markup=_toggles_keyboard(inputs, back_cb="gkiback:sub", selected_target=selected_target),
            parse_mode="HTML"
        )
        return GKI_TOGGLES

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

        # Build All: append to last row if space, else own row
        if user_is_admin:
            all_icon = "✅" if all_selected else "⬜"
            all_btn = InlineKeyboardButton(f"{all_icon} All", callback_data="gkisub:all")
            if row and len(row) < 4:
                row.append(all_btn)
                rows.append(row)
            else:
                if row:
                    rows.append(row)
                rows.append([all_btn])
        else:
            if row:
                rows.append(row)

        # Nav: [⬅️] [❌] [➡️]
        nav = [InlineKeyboardButton("⬅️", callback_data="gkiback:target")]
        nav.append(InlineKeyboardButton("❌", callback_data="gki:cancel"))
        if user_is_admin:
            nav.append(InlineKeyboardButton("➡️", callback_data="gkisub:done"))
        rows.append(nav)
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
            # User: chọn 1 sub → vào toggles để chọn tính năng trước khi confirm
            context.user_data["gki"]["selected_subs"] = {val}
            context.user_data["gki"]["inputs"]["sub_levels"] = str(val)
            header = _task_header(context)
            inputs = context.user_data["gki"]["inputs"]
            selected_target = context.user_data["gki"].get("selected_target", "")
            await q.edit_message_text(
                header + "<b>Tùy chỉnh tính năng:</b>",
                reply_markup=_toggles_keyboard(inputs, back_cb="gkiback:sub", selected_target=selected_target),
                parse_mode="HTML"
            )
            return GKI_TOGGLES

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
            # Admin: đã chọn sub xong → vào Toggles
            header = _task_header(context)
            inputs = context.user_data["gki"]["inputs"]
            selected_target = context.user_data["gki"].get("selected_target", "")
            await q.edit_message_text(
                header + "<b>Tùy chỉnh tính năng:</b>",
                reply_markup=_toggles_keyboard(inputs, back_cb="gkiback:sub", selected_target=selected_target),
                parse_mode="HTML"
            )
            return GKI_TOGGLES

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
        user_is_admin = await is_admin(q.from_user.id, self.storage)
        back_data = "gkiback:release" if user_is_admin else "gkiback:toggles"

        # Dịch targets
        targets = []
        if inputs.get("build_all"):
            targets = ["Tất cả"]
        else:
            if inputs.get("build_a12_5_10"):  targets.append("Android 12 (5.10)")
            if inputs.get("build_a13_5_15"):  targets.append("Android 13 (5.15)")
            if inputs.get("build_a14_6_1"):   targets.append("Android 14 (6.1)")
            if inputs.get("build_a15_6_6"):   targets.append("Android 15 (6.6)")
            if inputs.get("build_a16_6_12"):  targets.append("Android 16 (6.12)")

        branch_raw = str(inputs.get("kernelsu_branch", "Stable"))
        branch = branch_raw.replace("(标准)", "(Stable)").replace("(开发)", "(Dev)")

        rt_map = {"Actions": "GitHub Actions", "Pre-Release": "Pre-Release", "Release": "Release"}
        release = rt_map.get(inputs.get("release_type", "Actions"), inputs.get("release_type", ""))

        subs = inputs.get("sub_levels", "")
        subs_display = "Tất cả" if not subs or str(subs).lower() in ("all", "*", "") else str(subs)

        ver = str(inputs.get("version", "")).strip("-").strip()
        ver_display = ver if ver else "(mặc định)"

        def flag(val): return "✅ Bật" if val else "❌ Tắt"

        target_key = context.user_data["gki"].get("selected_target", "")
        show_supp_op = target_key in SUPP_OP_TARGETS
        lines = [
            f"• Kernel Build: <b>{inputs.get('kernelsu_variant', '-')}</b>",
            f"• Branch: {branch}",
            f"• Custom version: {ver_display}",
            f"• KernelSU version: {', '.join(targets) if targets else '(chưa chọn)'}",
            f"• Sub-level: {subs_display}",
            f"• ZRAM: {flag(inputs.get('use_zram', True))}",
            f"• BBG: {flag(inputs.get('use_bbg', True))}",
            f"• KPM: {flag(inputs.get('use_kpm', True))}",
            f"• SUSFS: {flag(not inputs.get('cancel_susfs', False))}",
        ]
        if show_supp_op:
            lines.append(f"• OnePlus 8E: {flag(inputs.get('supp_op', False))}")
        lines.append(f"• Loại release: {release}")
        _ = lines  # reassign below
        lines_final = lines
        pretty = "\n".join(lines_final)

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Xác nhận", callback_data="gkiconfirm")],
            [InlineKeyboardButton("⬅️", callback_data=back_data), InlineKeyboardButton("❌", callback_data="gki:cancel")]
        ])
        header = _task_header(context)
        await q.edit_message_text(
            header + f"<b>Xác nhận build GKI</b>\n\n{pretty}",
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

        # Smart dispatch: use kernel-custom.yml for single target + single sub_level
        target_keys = [k for k in ("build_a12_5_10","build_a13_5_15","build_a14_6_1","build_a15_6_6","build_a16_6_12") if inputs.get(k)]
        sub_levels_str = str(inputs.get("sub_levels", "")).strip()
        sub_list = [s.strip() for s in sub_levels_str.split(",") if s.strip()] if sub_levels_str else []
        dispatch_file = wf
        dispatch_inputs = inputs
        if (len(target_keys) == 1 and len(sub_list) == 1
                and not inputs.get("build_all") and target_keys[0] in TARGET_META):
            t_key = target_keys[0]
            sl = sub_list[0]
            android_ver, kernel_ver = TARGET_META[t_key]
            meta = SUB_LEVEL_META.get(t_key, {}).get(sl, ("lts", ""))
            dispatch_file = CUSTOM_WORKFLOW
            dispatch_inputs = {
                "android_version": android_ver,
                "kernel_version":  kernel_ver,
                "sub_level":       sl,
                "os_patch_level":  meta[0],
                "revision":        meta[1],
                "kernelsu_variant": inputs.get("kernelsu_variant", "SukiSU"),
                "kernelsu_branch":  inputs.get("kernelsu_branch", "Stable(标准)"),
                "version":          inputs.get("version", ""),
                "use_zram":         inputs.get("use_zram", False),
                "use_bbg":          inputs.get("use_bbg", False),
                "use_kpm":          inputs.get("use_kpm", False),
                "cancel_susfs":     inputs.get("cancel_susfs", True),
                "supp_op":          inputs.get("supp_op", False) if t_key in SUPP_OP_TARGETS else False,
            }

        res = await self.gh.dispatch_workflow(
            repo=self.config.GKI_REPO,
            workflow_file=dispatch_file,
            ref=self.config.GKI_DEFAULT_BRANCH,
            inputs=dispatch_inputs
        )
        if res["status"] in (201, 202, 204):
            job = {
                "type": "gki",
                "repo": self.config.GKI_REPO,
                "workflow_file": dispatch_file,
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
            view_url = f"https://github.com/{self.config.GITHUB_OWNER}/{self.config.GKI_REPO}/actions/workflows/{dispatch_file}"
            btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 Github", url=view_url),
                InlineKeyboardButton("📊 Dashboard", url="https://kernel.takeshi.dev/")
            ]])
            
            clean_name = user.full_name.replace("#", "＃").replace("@", "＠").replace("<", "&lt;").replace(">", "&gt;")
            mention = f'<a href="tg://user?id={user.id}">{clean_name}</a>'
            
            msg_text = (
                f"✅ <b>Đã gửi build thành công!</b>\n"
                f"👤 Người gửi: {mention}\n\n"
                f"<i>Bạn sẽ nhận được thông báo khi hoàn tất.</i>"
            )
            await q.edit_message_text(msg_text, reply_markup=btn, parse_mode="HTML")
            
            if str(user.id) != str(self.config.OWNER_ID):
                await send_admin_notification(
                    self.config.TELEGRAM_BOT_TOKEN,
                    self.config.OWNER_ID,
                    mention,
                    view_url,
                    job_type="GKI"
                )
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
            GKI_TOGGLES: [CallbackQueryHandler(flow.toggle_feature, pattern=r"^gkitog:.+$"), back_handler, cancel_handler],
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
