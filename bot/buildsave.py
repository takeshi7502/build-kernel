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
BS_VARIANT, BS_TARGET, BS_SUB, BS_CONFIRM = range(10, 14)

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

        subs = SUB_LEVELS.get(target_key, [])
        variant = context.user_data["bs"]["variant"]

        rows, row = [], []
        for i, sv in enumerate(subs, 1):
            # Format full kernel version: 5.10.xxx
            _, kernel_ver = TARGET_META.get(target_key, ("", "?"))
            btn_label = f"{kernel_ver}.{sv}" if sv != "X" else f"{kernel_ver}.X (LTS)"
            row.append(InlineKeyboardButton(btn_label, callback_data=f"bssub:{sv}"))
            if len(row) == 3:
                rows.append(row); row = []
        if row:
            rows.append(row)
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="bsback:target"),
            InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
        ])

        await q.edit_message_text(
            f"🔨 <b>Build Kernel Lưu Trữ</b>\n"
            f"• Variant: <b>{variant}</b>\n"
            f"• Target: <b>{label}</b>\n\n"
            "Chọn <b>Sub-level</b>:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_SUB

    # ─── Chọn Sub-level ─────────────────────────────────────────────
    async def set_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        sv = q.data.split(":", 1)[1]
        bs = context.user_data["bs"]
        bs["sub_level"] = sv
        variant = bs["variant"]
        target_key = bs["target_key"]
        target_label = bs["target_label"]
        _, kernel_ver = TARGET_META.get(target_key, ("", "?"))
        full_ver = f"{kernel_ver}.{sv}" if sv != "X" else f"{kernel_ver}.LTS"

        await q.edit_message_text(
            f"🔨 <b>Build Kernel Lưu Trữ — Xác nhận</b>\n\n"
            f"• Variant: <b>{variant}</b>\n"
            f"• Target: <b>{target_label}</b>\n"
            f"• Sub-level: <b>{full_ver}</b>\n\n"
            f"<i>Cấu hình cố định: Stable | ZRAM/BBG/SUSFS ✅ | KPM ❌</i>\n\n"
            f"⚠️ Build này sẽ <b>tự động cập nhật link tải xuống lên web</b> khi hoàn tất.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Xác nhận Build", callback_data="bs:confirm")],
                [
                    InlineKeyboardButton("⬅️", callback_data="bsback:sub"),
                    InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
                ],
            ]),
            parse_mode="HTML"
        )
        return BS_CONFIRM

    # ─── Confirm → Dispatch ─────────────────────────────────────────
    async def do_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query; await q.answer()
        user = update.effective_user
        bs = context.user_data.get("bs", {})

        variant = bs.get("variant", "SukiSU")
        target_key = bs.get("target_key", "")
        sv = bs.get("sub_level", "")

        android_ver, kernel_ver = TARGET_META.get(target_key, ("", ""))
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

        res = await self.gh.dispatch_workflow(
            repo=self.config.GKI_REPO,
            workflow_file=CUSTOM_WORKFLOW,
            ref=self.config.GKI_DEFAULT_BRANCH,
            inputs=dispatch_inputs,
        )

        if res["status"] not in (201, 202, 204):
            await q.edit_message_text(
                f"⚠️ Dispatch lỗi: {res['status']} {res.get('json')}"
            )
            return ConversationHandler.END

        job = {
            "type":          "buildsave",          # phân biệt với gki/oki
            "repo":          self.config.GKI_REPO,
            "workflow_file": CUSTOM_WORKFLOW,
            "ref":           self.config.GKI_DEFAULT_BRANCH,
            "inputs":        dispatch_inputs,
            "user_id":       user.id,
            "user_name":     user.full_name,
            "chat_id":       update.effective_chat.id,
            "created_at":    datetime.now(timezone.utc).isoformat(),
            "run_id":        None,
            "status":        "dispatched",
            "conclusion":    None,
            "notified":      False,
            # Metadata riêng cho buildsave
            "bs_variant":    variant,
            "bs_android":    android_ver,
            "bs_kernel_ver": kernel_ver,
            "bs_sub_level":  sv,
            "bs_full_ver":   full_ver,
        }
        await self.storage.add_job(job)

        view_url = (
            f"https://github.com/{self.config.GITHUB_OWNER}"
            f"/{self.config.GKI_REPO}/actions/workflows/{CUSTOM_WORKFLOW}"
        )
        clean_name = user.full_name.replace("#", "＃").replace("@", "＠")
        mention = f'<a href="tg://user?id={user.id}">{clean_name}</a>'

        await q.edit_message_text(
            f"✅ <b>Đã gửi lệnh build lưu trữ!</b>\n"
            f"🔨 {variant} <b>{full_ver}</b>\n"
            f"👤 Người gửi: {mention}\n\n"
            f"<i>Bot sẽ thông báo và tự cập nhật link web khi hoàn tất.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 GitHub", url=view_url),
                InlineKeyboardButton("🌐 Web", url="https://kernel.takeshi.dev/"),
            ]]),
            parse_mode="HTML"
        )
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
            return await self.set_target_from_back(q, context)
        return BS_CONFIRM

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
        variant = bs.get("variant", "")
        target_key = bs.get("target_key", "")
        target_label = bs.get("target_label", "")
        subs = SUB_LEVELS.get(target_key, [])
        _, kernel_ver = TARGET_META.get(target_key, ("", "?"))

        rows, row = [], []
        for i, sv in enumerate(subs, 1):
            btn_label = f"{kernel_ver}.{sv}" if sv != "X" else f"{kernel_ver}.X (LTS)"
            row.append(InlineKeyboardButton(btn_label, callback_data=f"bssub:{sv}"))
            if len(row) == 3:
                rows.append(row); row = []
        if row:
            rows.append(row)
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="bsback:target"),
            InlineKeyboardButton("❌ Hủy", callback_data="bs:cancel"),
        ])
        await q.edit_message_text(
            f"🔨 <b>Build Kernel Lưu Trữ</b>\n• Variant: <b>{variant}</b>\n• Target: <b>{target_label}</b>\n\nChọn <b>Sub-level</b>:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="HTML"
        )
        return BS_SUB

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
            BS_SUB:      [CallbackQueryHandler(flow.set_sub,     pattern=r"^bssub:.+$"), back_h, cancel_h],
            BS_CONFIRM:  [CallbackQueryHandler(flow.do_dispatch,  pattern=r"^bs:confirm$"), back_h, cancel_h],
        },
        fallbacks=[cancel_h],
        allow_reentry=True,
        name="buildsave_conversation",
        persistent=False,
    )
