# ==============================
# GitHub Workflow Bot - oki.py
# ==============================
from typing import Dict, Any, List
from datetime import datetime, timezone
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, CommandHandler, filters
)
from permissions import is_admin

# States
(
    OKI_START,
    OKI_CHOOSE_FILE,
    OKI_CHOOSE_KSU_VARIANT,
    OKI_CHOOSE_KPM,
    OKI_CHOOSE_MANAGER,
    OKI_CONFIRM
) = range(6)

FILES = [
  "oneplus_nord_n30_se_5g_v", "oneplus_10r_v", "oneplus_nord_3_v", "oneplus_ace_v", "oneplus_ace_race_v",
  "oneplus_10_pro_b", "oneplus_10t_v", "oneplus_11r_b", "oneplus_ace2_b", "oneplus_pad_lite_v", "oneplus_pad_mt6983_b",
  "oneplus_ace_2v_b", "oneplus_ace_pro_v", "oneplus_11_b", "oneplus_12r_b", "oneplus_ace2_pro_b", "oneplus_ace3_b",
  "oneplus_open_b", "oneplus_nord_ce4_b", "oneplus_12_b", "oneplus_pad_go_2_b", "oneplus_nord_ce4_lite_5g_b",
  "oneplus_nord_4_b", "oneplus_ace_3v_b", "oneplus_pad_mt6897_v", "oneplus_13r_b", "oneplus_ace3_pro_b",
  "oneplus_ace5_b", "oneplus_pad_pro_b", "oneplus_pad2_b", "oneplus_nord_ce5_b", "oneplus_nord_5_b",
  "oneplus_ace5_pro_b", "oneplus_13_b", "oneplus_13t_b", "oneplus_13s_b", "oneplus_pad_2_pro_b", "oneplus_pad_3_b",
  "oneplus_ace5_race_b", "oneplus_ace5_ultra_b", "oneplus_ace5_ultra_bak_b", "oneplus_pad2_mt6991_b",
  "oneplus_ace_6", "oneplus_ace_6t", "oneplus_ace_6t_aosp", "oneplus_15r", "oneplus_15r_aosp", "oneplus_15", "oneplus_15_aosp"
]

def _clean_label(s: str) -> str:
    if s.startswith("oneplus_"):
        s = s[len("oneplus_"):]
    if s.endswith("_v"):
        s = s[:-2]
    if s.endswith("_b"):
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

def _back_cancel(back_to: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Quay lại", callback_data=f"okiback:{back_to}")],
        [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
    ])

async def _ensure_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    owner = context.chat_data.get("oki_owner")
    if owner is None or owner == uid:
        return True
    try:
        if update.callback_query:
            await update.callback_query.answer("Phiên này không thuộc về bạn.", show_alert=True)
    except Exception:
        pass
    return False

async def _del_msg_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
    except:
        pass

def _cleanup(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("oki", None)
    context.user_data.pop("build_key", None)
    context.user_data.pop("oki_bot_msg_id", None)
    context.chat_data.pop("oki_owner", None)


class OKIFlow:
    def __init__(self, gh, storage, config):
        self.gh = gh
        self.storage = storage
        self.config = config

    async def _send_msg(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
        kwargs.pop('quote', None)
        chat_id = update.effective_chat.id
        thread_id = update.effective_message.message_thread_id if (update.effective_message and update.effective_message.is_topic_message) else None
        return await context.bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text, **kwargs)

    async def _update_bot_msg(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
        kwargs.pop("quote", None)
        chat_id = update.effective_chat.id
        thread_id = update.effective_message.message_thread_id if (update.effective_message and update.effective_message.is_topic_message) else None
        bot_msg_id = context.user_data.get("oki_bot_msg_id")

        if bot_msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=bot_msg_id,
                    text=text,
                    **kwargs
                )
                return
            except Exception:
                pass

        msg = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            **kwargs
        )
        context.user_data["oki_bot_msg_id"] = msg.message_id

    async def _safe_delete_user_msg(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            try:
                await update.message.delete()
            except:
                pass

    async def _check_user_job_limit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        user = update.effective_user
        if await is_admin(user.id, self.storage):
            return True

        if context.args:
            key = context.args[0]
            uses = await self.storage.get_uses(key)
            if uses > 0 and await self.storage.is_vip_key(key):
                return True

        active = await self.storage.list_user_active_jobs(user.id)
        if active:
            job_created_at = datetime.fromisoformat(active[0]["created_at"])
            elapsed = (datetime.now(timezone.utc) - job_created_at).total_seconds()
            if elapsed < 3600:
                remaining = int((3600 - elapsed) // 60) + 1
                m = await self._send_msg(update, context, f"⚠️ Chỉ được 1 job/1h. Vui lòng đợi {remaining} phút.")
                context.job_queue.run_once(_del_msg_job, when=45, chat_id=m.chat_id, data=m.message_id)
                return False
        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._safe_delete_user_msg(update, context)

        if not await self._check_user_job_limit(update, context):
            return ConversationHandler.END

        # Enforce key for non-admin
        text = update.message.text.strip() if update.message else ""
        parts = text.split(maxsplit=1)
        key = parts[1].strip() if len(parts) == 2 else None

        if not await is_admin(update.effective_user.id, self.storage):
            if not key:
                m = await self._send_msg(update, context, "Thiếu key. Dùng: /oki {key}")
                context.job_queue.run_once(_del_msg_job, when=30, chat_id=m.chat_id, data=m.message_id)
                return ConversationHandler.END
            
            uses = await self.storage.get_uses(key)
            if uses <= 0:
                m = await self._send_msg(update, context, f"❌ Key `{key}` không hợp lệ hoặc đã hết lượt.", parse_mode="Markdown")
                context.job_queue.run_once(_del_msg_job, when=30, chat_id=m.chat_id, data=m.message_id)
                return ConversationHandler.END

            context.user_data["build_key"] = key

        context.chat_data["oki_owner"] = update.effective_user.id
        # Mặc định tất cả các tham số
        context.user_data["oki"] = {"inputs": {
            "FILE": "oneplus_12_b",
            "MANAGER_SOURCE": "MIUIX",
            "SUSFS_CI": "N/A",  
            "KPM": "KPM",
            "SUSFS_META": "",
            "DYNAMIC_REPO": "Numbersf",
            "BUILD_TIME": "F",
            "KSU_META": "susfs-main/Numbersf/",
            "ZRAM": "0/lz4kd/8589934592",
            "SUFFIX": "",
            "SUBLEVEL": "",
            "FAST_BUILD": True,
            "LSM_BBG": True,
            "NETFILTER": True,
            "CCM": True,
            "UNICODE_BYPASS": False,
            "SCHED_HMBIRD": False,
            "SUSFS_DEV": False,
            "SPACE_NOCLEAN": False,
            "BUILD_NOCCACHE": False
        }}
        await self._update_bot_msg(update, context, "<b>Chọn máy bạn muốn build:</b>", parse_mode="HTML", reply_markup=_file_keyboard(0))
        return OKI_CHOOSE_FILE

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query and not await _ensure_owner(update, context):
            return ConversationHandler.END
        q = update.callback_query
        if q:
            await q.answer()
            await q.edit_message_text("Đã huỷ phiên OKI.")
        else:
            await self._update_bot_msg(update, context, "Đã huỷ phiên OKI.")
        _cleanup(context)
        return ConversationHandler.END

    async def back(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return
        q = update.callback_query; await q.answer()
        _, target = q.data.split(":", 1)
        
        if target == "file":
            await q.edit_message_text("<b>Chọn máy bạn muốn build:</b>", parse_mode="HTML", reply_markup=_file_keyboard(0))
            return OKI_CHOOSE_FILE
        elif target == "ksu":
            await self._ask_ksu(q)
            return OKI_CHOOSE_KSU_VARIANT
        elif target == "kpm":
            await self._ask_kpm(q)
            return OKI_CHOOSE_KPM
        elif target == "manager":
            await self._ask_manager(q)
            return OKI_CHOOSE_MANAGER

    async def page(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return OKI_CHOOSE_FILE
        q = update.callback_query; await q.answer()
        _, page = q.data.split(":", 1)
        page = int(page)
        await q.edit_message_text("<b>Chọn máy bạn muốn build:</b>", parse_mode="HTML", reply_markup=_file_keyboard(page))
        return OKI_CHOOSE_FILE

    async def set_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return OKI_CHOOSE_FILE
        q = update.callback_query; await q.answer()
        _, fileval = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["FILE"] = fileval
        await self._ask_ksu(q)
        return OKI_CHOOSE_KSU_VARIANT
        
    async def _ask_ksu(self, q):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("SukiSU (susfs-main)", callback_data="okiksuvar:susfs-main/Numbersf/")],
            [InlineKeyboardButton("NextSU (next nhánh)", callback_data="okiksuvar:next/Numbersf/")],
            [InlineKeyboardButton("ReSuKi (resuki nhánh)", callback_data="okiksuvar:resuki/Numbersf/")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="okiback:file")],
            [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
        ])
        await q.edit_message_text("<b>Chọn loại KernelSU bạn muốn tích hợp:</b>", parse_mode="HTML", reply_markup=kb)

    async def set_ksu_var(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return OKI_CHOOSE_KSU_VARIANT
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["KSU_META"] = val
        await self._ask_kpm(q)
        return OKI_CHOOSE_KPM

    async def _ask_kpm(self, q):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("KPM", callback_data="okikpm:KPM"), InlineKeyboardButton("KPN", callback_data="okikpm:KPN")],
            [InlineKeyboardButton("N/A (Tắt Module)", callback_data="okikpm:N/A")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="okiback:ksu")],
            [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
        ])
        await q.edit_message_text("<b>Chọn phương thức module KernelSU (KPM):</b>", parse_mode="HTML", reply_markup=kb)

    async def set_kpm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return OKI_CHOOSE_KPM
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["KPM"] = val
        await self._ask_manager(q)
        return OKI_CHOOSE_MANAGER

    async def _ask_manager(self, q):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("MIUIX", callback_data="okimgr:MIUIX"), InlineKeyboardButton("MIUIX_SPOOF", callback_data="okimgr:MIUIX_SPOOF")],
            [InlineKeyboardButton("MD3", callback_data="okimgr:MD3"), InlineKeyboardButton("MD3_SPOOF", callback_data="okimgr:MD3_SPOOF")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="okiback:kpm")],
            [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
        ])
        await q.edit_message_text("<b>Chọn nguồn Manager (Trình quản lý):</b>", parse_mode="HTML", reply_markup=kb)

    async def set_manager(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return OKI_CHOOSE_MANAGER
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        context.user_data["oki"]["inputs"]["MANAGER_SOURCE"] = val
        await self._ask_confirm(q)
        return OKI_CONFIRM

    async def _ask_confirm(self, q):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ BẬT Fast Build", callback_data="okiconf:fast"), InlineKeyboardButton("🐢 TẮT Fast Build", callback_data="okiconf:slow")],
            [InlineKeyboardButton("🔙 Quay lại", callback_data="okiback:manager")],
            [InlineKeyboardButton("❌ Hủy", callback_data="oki:cancel")]
        ])
        await q.edit_message_text("<b>Cấu hình cuối cùng: Chọn tốc độ Build (Fast Build):</b>\n<i>(Các cấu hình khác như ZRAM, BBG, DYNAMIC_REPO sẽ dùng mặc định)</i>", parse_mode="HTML", reply_markup=kb)

    async def set_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context): return OKI_CONFIRM
        q = update.callback_query; await q.answer()
        _, val = q.data.split(":", 1)
        
        inputs = context.user_data["oki"]["inputs"]
        inputs["FAST_BUILD"] = (val == "fast")
        
        await q.edit_message_text("⏳ Đang gửi request tới GitHub...")
        
        user = update.effective_user
        key = context.user_data.get("build_key")
        user_is_admin = await is_admin(user.id, self.storage)

        dispatch_file = self.config.OKI_WORKFLOW
        
        dispatch_inputs = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in inputs.items()}
        res = await self.gh.dispatch_workflow(
            repo=self.config.OKI_REPO,
            workflow_file=dispatch_file,
            ref=self.config.OKI_DEFAULT_BRANCH,
            inputs=dispatch_inputs
        )
        
        if res.get("status") == 204:
            job = {
                "type": "oki",
                "repo": self.config.OKI_REPO,
                "workflow_file": dispatch_file,
                "ref": self.config.OKI_DEFAULT_BRANCH,
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
            if not user_is_admin and key:
                await self.storage.consume(key)
            
            view_url = f"https://github.com/{self.config.GITHUB_OWNER}/{self.config.OKI_REPO}/actions/workflows/{dispatch_file}"
            btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 Github", url=view_url),
                InlineKeyboardButton("📊 Dashboard", url="https://kernel.takeshi.dev/")
            ]])
            
            mention = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
            
            msg_text = (
                f"✅ <b>Đã gửi OKI Build thành công!</b>\n"
                f"👤 Người gửi: {mention}\n\n"
                f"<i>Bạn sẽ nhận được thông báo khi hoàn tất.</i>"
            )
            await q.edit_message_text(msg_text, reply_markup=btn, parse_mode="HTML")
        else:
            m = await q.edit_message_text(f"⚠️ Dispatch lỗi: {res['status']} {res.get('json')}")
            if context.job_queue:
                context.job_queue.run_once(_del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id)
        
        _cleanup(context)
        return ConversationHandler.END


def build_oki_conversation(gh, storage, config):
    flow = OKIFlow(gh, storage, config)
    cancel_handler = CallbackQueryHandler(flow.cancel, pattern=r"^oki:cancel$")
    back_handler = CallbackQueryHandler(flow.back, pattern=r"^okiback:.+$")
    return ConversationHandler(
        entry_points=[CommandHandler("oki", flow.start)],
        states={
            OKI_CHOOSE_FILE: [CallbackQueryHandler(flow.page, pattern=r"^okipage:"), CallbackQueryHandler(flow.set_file, pattern=r"^okifile:"), back_handler, cancel_handler],
            OKI_CHOOSE_KSU_VARIANT: [CallbackQueryHandler(flow.set_ksu_var, pattern=r"^okiksuvar:"), back_handler, cancel_handler],
            OKI_CHOOSE_KPM: [CallbackQueryHandler(flow.set_kpm, pattern=r"^okikpm:"), back_handler, cancel_handler],
            OKI_CHOOSE_MANAGER: [CallbackQueryHandler(flow.set_manager, pattern=r"^okimgr:"), back_handler, cancel_handler],
            OKI_CONFIRM: [CallbackQueryHandler(flow.set_confirm, pattern=r"^okiconf:"), back_handler, cancel_handler],
        },
        fallbacks=[cancel_handler],
        per_user=True,
        per_chat=False,
        conversation_timeout=300
    )
