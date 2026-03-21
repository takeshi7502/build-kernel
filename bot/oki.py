# ==============================
# GitHub Workflow Bot - oki.py
# ==============================
from typing import Dict, Any, List
from datetime import datetime, timezone
import time, math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, CommandHandler, filters
)

# States
(
    OKI_START,
    OKI_CHOOSE_FILE,
    OKI_HOOK,
    OKI_SUSFS,
    OKI_KSU_META,
    OKI_BUILD_TIME,
    OKI_SUFFIX,
    OKI_FAST_BUILD,
    OKI_LSM,
    OKI_SCHED,
    OKI_ZRAM,
    OKI_CONFIRM
) = range(12)

FILES = [
  "oneplus_nord_ce4_lite_5g_v","oneplus_nord_ce4_v","oneplus_nord_4_v","oneplus_ace_3v_v","oneplus_10_pro_v",
  "oneplus_10t_v","oneplus_11r_v","oneplus_ace2_v","oneplus_ace_pro_v","oneplus_11_v","oneplus_12r_v",
  "oneplus_ace2_pro_v","oneplus_ace3_v","oneplus_open_v","oneplus12_v","oneplus_13r","oneplus_ace3_pro_v",
  "oneplus_ace5","oneplus_pad_pro_v","oneplus_pad2_v","oneplus_nord_5","oneplus_ace5_pro","oneplus_13",
  "oneplus_13_global","oneplus_13t","oneplus_13s","oneplus_pad_2_pro","oneplus_pad_3","oneplus_ace5_ultra"
]

def _clean_label(s: str) -> str:
    if s.startswith("oneplus_"):
        s = s[len("oneplus_"):]
    if s.endswith("_v"):
        s = s[:-2]
    return s.replace("_", " ")

def _paginate(items: List[str], page: int, per_page: int = 12):
    import math
    total_pages = max(1, math.ceil(len(items) / per_page))
    start = page * per_page
    end = min(len(items), start + per_page)
    return items[start:end], total_pages

def _file_keyboard(page: int = 0):
    page_items, total_pages = _paginate(FILES, page, per_page=12)
    rows = []
    row = []
    for i, val in enumerate(page_items, start=1):
        label = _clean_label(val)
        row.append(InlineKeyboardButton(label, callback_data=f"okifile:{val}"))
        if i % 3 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"okipage:{page-1}"))
    if page < total_pages-1: nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"okipage:{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")])
    return InlineKeyboardMarkup(rows)


def _yes_no(prefix: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Bật", callback_data=f"{prefix}:true"),
            InlineKeyboardButton("❌ Tắt", callback_data=f"{prefix}:false"),
        ],
        [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
    ])


def _ensure_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    owner = context.chat_data.get("oki_owner")
    if owner is None or owner == uid:
        return True
    try:
        update.callback_query.answer("Phiên này không thuộc về bạn.", show_alert=True)
    except Exception:
        pass
    return False


class OKIFlow:
    def __init__(self, gh, storage, config):
        self.gh = gh
        self.storage = storage
        self.config = config

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Enforce key for non-admin
        text = update.message.text.strip() if update.message else ""
        parts = text.split(maxsplit=1)
        key = parts[1].strip() if len(parts) == 2 else None

        if int(update.effective_user.id) != int(self.config.OWNER_ID):
            if not key:
                await update.message.reply_text("Thiếu key. Dùng: /oki {key}")
                return ConversationHandler.END
            context.user_data["build_key"] = key

        context.chat_data["oki_owner"] = update.effective_user.id
        context.user_data["oki"] = {"inputs": {
            "HOOK": "manual",
            "SUSFS_CI": "NoN",
            "KSU_META": "susfs-main/Numbersf/",
            "BUILD_TIME": "F",
            "SUFFIX": "",
            "FAST_BUILD": True,
            "LSM": True,
            "SCHED": False,
            "ZRAM": False
        }}
        await update.message.reply_text("Chọn máy:", reply_markup=_file_keyboard(0))
        return OKI_CHOOSE_FILE

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query and not _ensure_owner(update, context):
            return ConversationHandler.END
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("Đã huỷ phiên.")
        context.user_data.pop("oki", None)
        context.user_data.pop("build_key", None)
        context.chat_data.pop("oki_owner", None)
        return ConversationHandler.END

    async def page(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_CHOOSE_FILE
        q = update.callback_query; await q.answer()
        _, page = q.data.split(":", 1)
        page = int(page)
        await q.edit_message_text("Chọn máy:", reply_markup=_file_keyboard(page))
        return OKI_CHOOSE_FILE

    async def set_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_CHOOSE_FILE
        q = update.callback_query; await q.answer()
        _, fileval = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["FILE"] = fileval
        await q.edit_message_text("Chọn HOOK (khuyến khích manual):", reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("kprobe", callback_data="okihook:kprobe"),
            InlineKeyboardButton("manual", callback_data="okihook:manual"),
            InlineKeyboardButton("tracepoint", callback_data="okihook:tracepoint"),
        ], [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]]))
        return OKI_HOOK

    async def set_hook(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_HOOK
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["HOOK"] = val
        await q.edit_message_text("Chọn SUSFS_CI (khuyến khích CI hoặc Release):", reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("CI", callback_data="okisci:CI"),
            InlineKeyboardButton("Release", callback_data="okisci:Release"),
            InlineKeyboardButton("NoN", callback_data="okisci:NoN"),
        ], [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]]))
        return OKI_SUSFS

    async def set_susfs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_SUSFS
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["SUSFS_CI"] = val
        await q.edit_message_text("Nhập `KSU_META` (reply). Nên sử dụng `susfs-main/Numbersf/`.", parse_mode="Markdown")
        return OKI_KSU_META

    async def set_ksu_meta(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = (update.message.text or "").strip()
        context.user_data["oki"]["inputs"]["KSU_META"] = "" if txt.lower() == "none" else txt
        await update.message.reply_text("Nhập `BUILD_TIME` (reply). Nhập `F` để dùng thời gian hiện tại. Gõ 'none' để bỏ qua.", parse_mode="Markdown")
        return OKI_BUILD_TIME

    async def set_build_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = (update.message.text or "").strip()
        if txt.upper() == "F":
            import time
            t = time.gmtime()
            txt = time.strftime("%a %b %d %H:%M:%S UTC %Y", t)
        context.user_data["oki"]["inputs"]["BUILD_TIME"] = "" if txt.lower() == "none" else txt
        await update.message.reply_text("Nhập `SUFFIX` (reply). Tên sẽ dạng 5.10.209-android12-yourname.", parse_mode="Markdown")
        return OKI_SUFFIX

    async def set_suffix(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        txt = (update.message.text or "").strip()
        context.user_data["oki"]["inputs"]["SUFFIX"] = "" if txt.lower() == "none" else txt
        await update.message.reply_text("Bật FAST_BUILD?, nên bật", reply_markup=_yes_no("okifast"))
        return OKI_FAST_BUILD

    async def set_fast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_FAST_BUILD
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["FAST_BUILD"] = (val == "true")
        await q.edit_message_text("Bật LSM?, nên bật", reply_markup=_yes_no("okilsm"))
        return OKI_LSM

    async def set_lsm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_LSM
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["LSM"] = (val == "true")
        await q.edit_message_text("Bật SCHED?, nên tắt", reply_markup=_yes_no("okischd"))
        return OKI_SCHED

    async def set_sched(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_SCHED
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["SCHED"] = (val == "true")
        await q.edit_message_text("Bật ZRAM?, nên tắt", reply_markup=_yes_no("okizram"))
        return OKI_ZRAM

    async def set_zram(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_ZRAM
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["ZRAM"] = (val == "true")
        return await self.confirm(q, context)

    async def confirm(self, q, context):
        inputs = context.user_data["oki"]["inputs"]
        pretty = "\n".join([f"• {k}: {v}" for k, v in inputs.items()])
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Xác nhận", callback_data="okiconfirm")],
            [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
        ])
        file_label = inputs.get("FILE", "?")
        await q.edit_message_text(f"OKI — FILE: `{file_label}`\nInputs:\n{pretty}", reply_markup=kb, parse_mode="Markdown")
        return OKI_CONFIRM

    async def do_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _ensure_owner(update, context): return OKI_CONFIRM
        q = update.callback_query; await q.answer()
        inputs = context.user_data["oki"]["inputs"].copy()
        user = update.effective_user

        # Admin bypass key; else check key exists and consume AFTER success
        key = context.user_data.get("build_key")
        if int(user.id) != int(self.config.OWNER_ID):
            uses = await self.storage.get_uses(key or "")
            if not key or uses <= 0:
                await q.edit_message_text("Key không hợp lệ hoặc hết lượt. Dừng.")
                context.user_data.pop("oki", None)
                context.user_data.pop("build_key", None)
                context.chat_data.pop("oki_owner", None)
                return ConversationHandler.END

        res = await self.gh.dispatch_workflow(
            repo=self.config.OKI_REPO,
            workflow_file=self.config.OKI_WORKFLOW,
            ref=self.config.OKI_DEFAULT_BRANCH,
            inputs=inputs
        )
        if res["status"] in (201, 202, 204):
            job = {
                "type": "oki",
                "repo": self.config.OKI_REPO,
                "workflow_file": self.config.OKI_WORKFLOW,
                "branch": self.config.OKI_DEFAULT_BRANCH,
                "inputs": inputs,
                "user_id": user.id,
                "chat_id": update.effective_chat.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "run_id": None,
                "status": "dispatched",
                "conclusion": None,
                "notified": False
            }
            job_id = await self.storage.add_job(job)
            if int(user.id) != int(self.config.OWNER_ID):
                await self.storage.consume(key)
            view_url = f"https://github.com/{self.config.GITHUB_OWNER}/{self.config.OKI_REPO}/actions/workflows/{self.config.OKI_WORKFLOW}"
            await q.edit_message_text(f"✅ Đã gửi build OKI!\nMình sẽ ping khi xong.\nXem: {view_url}")
        else:
            await q.edit_message_text(f"⚠️ Dispatch lỗi: {res['status']} {res.get('json')}")

        context.user_data.pop("oki", None)
        context.user_data.pop("build_key", None)
        context.chat_data.pop("oki_owner", None)
        return ConversationHandler.END


def build_oki_conversation(gh, storage, config):
    flow = OKIFlow(gh, storage, config)
    return ConversationHandler(
        entry_points=[CommandHandler("oki", flow.start)],
        states={
            OKI_CHOOSE_FILE: [
                CallbackQueryHandler(flow.set_file, pattern=r"^okifile:.+"),
                CallbackQueryHandler(flow.page, pattern=r"^okipage:\d+$"),
                CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")
            ],
            OKI_HOOK: [CallbackQueryHandler(flow.set_hook, pattern=r"^okihook:(kprobe|manual|tracepoint)$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
            OKI_SUSFS: [CallbackQueryHandler(flow.set_susfs, pattern=r"^okisci:(CI|Release|NoN)$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
            OKI_KSU_META: [MessageHandler(filters.TEXT & ~filters.COMMAND, flow.set_ksu_meta)],
            OKI_BUILD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, flow.set_build_time)],
            OKI_SUFFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, flow.set_suffix)],
            OKI_FAST_BUILD: [CallbackQueryHandler(flow.set_fast, pattern=r"^okifast:(true|false)$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
            OKI_LSM: [CallbackQueryHandler(flow.set_lsm, pattern=r"^okilsm:(true|false)$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
            OKI_SCHED: [CallbackQueryHandler(flow.set_sched, pattern=r"^okischd:(true|false)$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
            OKI_ZRAM: [CallbackQueryHandler(flow.set_zram, pattern=r"^okizram:(true|false)$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
            OKI_CONFIRM: [CallbackQueryHandler(flow.do_dispatch, pattern=r"^okiconfirm$"), CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
        },
        fallbacks=[CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")],
        allow_reentry=True,
        name="oki_conversation",
        persistent=False,
    )