"""
buildsave.py — /build command: Build kernel cố định để lưu trữ trên web.

Flow:
  /build → Chọn Variant → Chọn Android Target → Chọn Sub-level → Confirm → Dispatch
Cấu hình cố định: Stable, no custom version, ZRAM/BBG/SUSFS=on, KPM=off, Actions release.
"""

from typing import Dict, Any
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler, CommandHandler
)
from permissions import is_admin
from config import send_admin_notification
from gki import (
    VARIANTS, TARGET_META, SUB_LEVELS, SUB_LEVEL_META,
    CUSTOM_WORKFLOW, _del_msg_job
)

# States
BS_VARIANT = 1
BS_TARGET  = 2
BS_SUB     = 3

# Cấu hình cố định cho build lưu trữ
_FIXED_CONFIG = {
    "kernelsu_branch":  "Stable(标准)",
    "version":          "",          # mặc định
    "use_zram":         True,
    "use_bbg":          True,
    "use_kpm":          False,
    "cancel_susfs":     False,       # SUSFS bật
    "release_type":     "Actions",
}

_TARGET_LABELS = [
    ("A12 — 5.10",  "build_a12_5_10"),
    ("A13 — 5.15",  "build_a13_5_15"),
    ("A14 — 6.1",   "build_a14_6_1"),
    ("A15 — 6.6",   "build_a15_6_6"),
    ("A16 — 6.12",  "build_a16_6_12"),
]

def _cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel")]])

def _back_cancel_kb(back_cb: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Quay lại", callback_data=back_cb),
        InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
    ]])


class BuildSaveFlow:
    def __init__(self, gh, storage, config):
        self.gh = gh
        self.storage = storage
        self.config = config

    # ─── /build entry ────────────────────────────────────────────────
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        storage = self.storage
        if not await is_admin(user.id, storage):
            return ConversationHandler.END

        context.user_data["bs"] = {}

        rows = []
        for i, v in enumerate(VARIANTS, 1):
            row_idx = (i - 1) // 3
            while len(rows) <= row_idx:
                rows.append([])
            rows[row_idx].append(InlineKeyboardButton(v, callback_data=f"bsvar:{v}"))
        rows.append([InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel")])

        try:
            await update.message.delete()
        except Exception:
            pass
        await update.effective_chat.send_message(
            "🔨 <b>Build Kernel Lưu Trữ</b>\n\nChọn <b>KernelSU Variant</b>:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_VARIANT

    # ─── Chọn Variant ────────────────────────────────────────────────
    async def set_variant(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        variant = q.data.split(":", 1)[1]
        context.user_data["bs"]["variant"] = variant

        rows = []
        for i, (label, key) in enumerate(_TARGET_LABELS, 1):
            row_idx = (i - 1) // 2
            while len(rows) <= row_idx:
                rows.append([])
            rows[row_idx].append(InlineKeyboardButton(label, callback_data=f"bstgt:{key}"))
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="bsback:variant"),
            InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
        ])

        await q.edit_message_text(
            f"🔨 <b>Build Kernel Lưu Trữ</b>\n"
            f"• Variant: <b>{variant}</b>\n\n"
            "Chọn <b>Android Target</b>:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_TARGET

    # ─── Chọn Target ─────────────────────────────────────────────────
    async def set_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        target_key = q.data.split(":", 1)[1]
        context.user_data["bs"]["target_key"] = target_key

        label = next((l for l, k in _TARGET_LABELS if k == target_key), target_key)
        context.user_data["bs"]["target_label"] = label
        context.user_data["bs"]["subs"] = []  # Khởi tạo list trống
        
        return await self.render_sub_menu(q, context)
        
    async def render_sub_menu(self, q, context):
        bs = context.user_data["bs"]
        target_key = bs.get("target_key", "")
        variant = bs.get("variant", "")
        label = bs.get("target_label", "")
        subs_list = SUB_LEVELS.get(target_key, [])
        selected = bs.get("subs", [])
        
        _, kernel_ver = TARGET_META.get(target_key, ("", "?"))
        rows, row = [], []
        
        for sv in subs_list:
            is_selected = sv in selected
            icon = "✅" if is_selected else "⬜"
            btn_label = f"{icon} {kernel_ver}.{sv}" if sv != "X" else f"{icon} {kernel_ver}.X (LTS)"
            row.append(InlineKeyboardButton(btn_label, callback_data=f"bstoggle:{sv}"))
            if len(row) == 3:
                rows.append(row); row = []
        if row: rows.append(row)
        
        # Nút chọn tất cả / Bỏ chọn
        if len(selected) == len(subs_list) and subs_list:
            rows.append([InlineKeyboardButton("✖️ Bỏ Chọn Tất Cả", callback_data="bstoggle:none")])
        else:
            rows.append([InlineKeyboardButton("✨ Chọn Tất Cả", callback_data="bstoggle:all")])
            
        # Nút xác nhận
        if selected:
            rows.append([InlineKeyboardButton(f"🚀 Chạy Build ({len(selected)} bản)", callback_data="bs:confirm")])
            
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="bsback:target"),
            InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
        ])
        
        await q.edit_message_text(
            f"🔨 <b>Build Kernel Lưu Trữ</b>\n"
            f"• Variant: <b>{variant}</b>\n"
            f"• Target: <b>{label}</b>\n\n"
            "Chọn <b>Sub-level</b> (chọn nhiều để xếp hàng đợi):",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_SUB

    # ─── Chọn Sub-level ─────────────────────────────────────────────
    async def toggle_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        sv = q.data.split(":", 1)[1]
        bs = context.user_data["bs"]
        
        target_key = bs["target_key"]
        subs_list = SUB_LEVELS.get(target_key, [])
        selected = bs.get("subs", [])
        
        if sv == "all":
            bs["subs"] = list(subs_list)
        elif sv == "none":
            bs["subs"] = []
        else:
            if sv in selected:
                selected.remove(sv)
            else:
                selected.append(sv)
                
        return await self.render_sub_menu(q, context)

    # ─── Confirm → Queue Dispatch ───────────────────────────────────
    async def do_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        import uuid
        q = update.callback_query; await q.answer()
        user = update.effective_user
        bs = context.user_data.get("bs", {})

        variant = bs.get("variant", "SukiSU")
        target_key = bs.get("target_key", "")
        selected_subs = bs.get("subs", [])
        
        if not selected_subs:
            await q.answer("Bạn chưa chọn bản nào!", show_alert=True)
            return BS_SUB

        android_ver, kernel_ver = TARGET_META.get(target_key, ("", ""))
        batch_id = str(uuid.uuid4())
        
        msg = await q.edit_message_text(
            f"⏳ Đang xếp hàng đợi {len(selected_subs)} bản build vào hệ thống..."
        )
        batch_msg_id = msg.message_id
        
        now_iso = datetime.now(timezone.utc).isoformat()
        jobs_to_create = []
        index = 1
        
        for sv in selected_subs:
            meta = SUB_LEVEL_META.get(target_key, {}).get(sv, ("lts", ""))
            full_ver = f"{kernel_ver}.{sv}" if sv != "X" else f"{kernel_ver}.X"

            dispatch_inputs = {
                "android_version":   android_ver,
                "kernel_version":    kernel_ver,
                "sub_level":         sv,
                "os_patch_level":    meta[0],
                "revision":          meta[1],
                "kernelsu_variant":  variant,
                "kernelsu_branch":   _FIXED_CONFIG["kernelsu_branch"],
                "version":           _FIXED_CONFIG["version"],
                "use_zram":          _FIXED_CONFIG["use_zram"],
                "use_bbg":           _FIXED_CONFIG["use_bbg"],
                "use_kpm":           _FIXED_CONFIG["use_kpm"],
                "cancel_susfs":      _FIXED_CONFIG["cancel_susfs"],
            }

            job = {
                "type":          "buildsave",
                "repo":          self.config.GKI_REPO,
                "workflow_file": CUSTOM_WORKFLOW,
                "ref":           self.config.GKI_DEFAULT_BRANCH,
                "inputs":        dispatch_inputs,
                "user_id":       user.id,
                "user_name":     user.full_name,
                "chat_id":       update.effective_chat.id,
                "created_at":    now_iso,
                "run_id":        None,
                "status":        "queued",
                "conclusion":    None,
                "notified":      False,
                
                "batch_id":      batch_id,
                "batch_msg_id":  batch_msg_id,
                "batch_total":   len(selected_subs),
                "batch_index":   index,
                
                "bs_variant":    variant,
                "bs_android":    android_ver,
                "bs_kernel_ver": kernel_ver,
                "bs_sub_level":  sv,
                "bs_full_ver":   full_ver,
            }
            jobs_to_create.append(job)
            index += 1

        for j in jobs_to_create:
             await self.storage.add_job(j)
             
        # Gửi thông điệp cập nhật lần đầu tiên
        from main import update_batch_message
        await update_batch_message(batch_id, self.storage, context.bot)

        context.user_data.pop("bs", None)
        return ConversationHandler.END

    # ─── Back navigation ────────────────────────────────────────────
    async def back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        step = q.data.split(":", 1)[1]
        if step == "variant":
            context.user_data["bs"] = {}
            return await self.start_from_query(q, context)
        elif step == "target":
            return await self.set_variant_from_back(q, context)
        elif step == "sub":
            bs = context.user_data["bs"]
            bs["subs"] = []
            return await self.render_sub_menu(q, context)

    async def start_from_query(self, q, context):
        rows = []
        for i, v in enumerate(VARIANTS, 1):
            row_idx = (i - 1) // 3
            while len(rows) <= row_idx:
                rows.append([])
            rows[row_idx].append(InlineKeyboardButton(v, callback_data=f"bsvar:{v}"))
        rows.append([InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel")])
        await q.edit_message_text(
            "🔨 <b>Build Kernel Lưu Trữ</b>\n\nChọn <b>KernelSU Variant</b>:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_VARIANT

    async def set_variant_from_back(self, q, context):
        variant = context.user_data["bs"].get("variant", "")
        rows = []
        for i, (label, key) in enumerate(_TARGET_LABELS, 1):
            row_idx = (i - 1) // 2
            while len(rows) <= row_idx:
                rows.append([])
            rows[row_idx].append(InlineKeyboardButton(label, callback_data=f"bstgt:{key}"))
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="bsback:variant"),
            InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
        ])
        await q.edit_message_text(
            f"🔨 <b>Build Kernel Lưu Trữ</b>\n• Variant: <b>{variant}</b>\n\nChọn <b>Android Target</b>:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_TARGET

    async def set_target_from_back(self, q, context):
        bs = context.user_data["bs"]
        return await self.render_sub_menu(q, context)

    # ─── Cancel ─────────────────────────────────────────────────────
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        context.user_data.pop("bs", None)
        await q.edit_message_text("❌ Đã hủy lệnh build lưu trữ.")
        return ConversationHandler.END


def build_buildsave_conversation(gh, storage, cfg):
    flow = BuildSaveFlow(gh, storage, cfg)
    cancel_h = CallbackQueryHandler(flow.cancel, pattern=r"^bs:cancel$")
    back_h = CallbackQueryHandler(flow.back, pattern=r"^bsback:.+$")
    return ConversationHandler(
        entry_points=[CommandHandler("build", flow.start)],
        states={
            BS_VARIANT:  [CallbackQueryHandler(flow.set_variant, pattern=r"^bsvar:.+$"), cancel_h],
            BS_TARGET:   [CallbackQueryHandler(flow.set_target,  pattern=r"^bstgt:.+$"), back_h, cancel_h],
            BS_SUB:      [
                CallbackQueryHandler(flow.toggle_sub,   pattern=r"^bstoggle:.+$"), 
                CallbackQueryHandler(flow.do_dispatch,  pattern=r"^bs:confirm$"), 
                back_h, 
                cancel_h
            ],
        },
        fallbacks=[cancel_h],
        allow_reentry=True,
        name="buildsave_conversation",
        persistent=False,
    )
