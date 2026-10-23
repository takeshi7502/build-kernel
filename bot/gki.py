import html
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .gki_contract import (
    MODE_CALLBACKS,
    MODE_FULL,
    MODE_NO_KSU,
    MODE_NO_SUSFS,
    MODES,
    SUB_LEVELS,
    SUPP_OP_TARGETS,
    TARGET_META,
    VARIANTS,
    cycle_feature,
    effective_mode,
    enabled,
    kpm_enabled,
    mode_label,
    normalize_inputs,
    prepare_dispatch,
)
from .permissions import is_admin

# States
(
    GKI_KSU_VARIANT,
    GKI_KSU_MODE,
    GKI_VERSION,
    GKI_TOGGLES,
    GKI_BUILD_TARGET,
    GKI_SUB_VERSION,
    GKI_RELEASE_TYPE,
    GKI_CONFIRM,
) = range(8)

BUILD_TARGETS = [
    (f"A{android.removeprefix('android')} - {kernel}", key)
    for key, (android, kernel) in TARGET_META.items()
]
RELEASE_TYPES = ["Actions", "Pre-Release", "Release"]


def _kb_from_list(prefix: str, values: List[str], back_cb: str = ""):
    rows, row = [], []
    for i, v in enumerate(values, start=1):
        row.append(InlineKeyboardButton(v, callback_data=f"{prefix}:{v}"))
        if i % 3 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if back_cb:
        rows.append(
            [
                InlineKeyboardButton("⬅️", callback_data=back_cb),
                InlineKeyboardButton("❌", callback_data="gki:cancel"),
            ]
        )
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
        rows.append(
            [
                InlineKeyboardButton("⬅️", callback_data=back_cb),
                InlineKeyboardButton("❌", callback_data="gki:cancel"),
            ]
        )
    else:
        rows.append([InlineKeyboardButton("❌", callback_data="gki:cancel")])
    return InlineKeyboardMarkup(rows)


def _build_target_keyboard(back_cb: str = ""):
    """Keyboard chọn 1 target, xếp 2 cột 2 hàng."""
    rows = []
    targets = [(label, key) for label, key in BUILD_TARGETS]
    for i in range(0, len(targets), 2):
        row = []
        for label, key in targets[i : i + 2]:
            row.append(InlineKeyboardButton(label, callback_data=f"gkitgt:{key}"))
        rows.append(row)
    if back_cb:
        rows.append(
            [
                InlineKeyboardButton("⬅️", callback_data=back_cb),
                InlineKeyboardButton("❌", callback_data="gki:cancel"),
            ]
        )
    else:
        rows.append([InlineKeyboardButton("❌", callback_data="gki:cancel")])
    return InlineKeyboardMarkup(rows)


TOGGLE_FEATURES = [
    ("KSU/SUSFS", "ksu_mode", "gkitog:mode"),
    ("ZRAM", "use_zram", "gkitog:zram"),
    ("BBG", "use_bbg", "gkitog:bbg"),
    ("KPM", "use_kpm", "gkitog:kpm"),
    ("Re-Kernel", "use_rekernel", "gkitog:rekernel"),
    ("NoMount", "use_nomount", "gkitog:nomount"),
    ("Networking", "use_networking", "gkitog:networking"),
    ("CVE-2026-43499", "cve_2026_43499_patch", "gkitog:cve"),
    ("Droidspaces", "droidspaces", "gkitog:droidspaces"),
    ("NTSync", "droidspaces_ntsync", "gkitog:ntsync"),
    ("Xuất patch SUSFS", "export_susfs_patches", "gkitog:export"),
    ("Support 1+ 8E", "supp_op", "gkitog:supp_op"),
    ("Artifacts", "artifact_upload_mode", "gkitog:artifacts"),
]


def _mode_keyboard():
    rows = [
        [InlineKeyboardButton(MODES[mode], callback_data=f"gkimode:{key}")]
        for key, mode in MODE_CALLBACKS.items()
    ]
    rows.append(
        [
            InlineKeyboardButton("⬅️", callback_data="gkiback:ksu_variant"),
            InlineKeyboardButton("❌", callback_data="gki:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _feature_buttons(inputs, selected_target="", *, buildsave=False):
    buttons = []
    for label, key, cb in TOGGLE_FEATURES:
        if key == "supp_op" and selected_target not in SUPP_OP_TARGETS:
            continue
        if key == "use_zram" and selected_target == "build_a16_6_12":
            continue
        if key == "export_susfs_patches" and (
            buildsave
            or effective_mode(inputs) != MODE_FULL
            or inputs.get("kernelsu_variant") not in {"SukiSU", "BakaSU"}
        ):
            continue
        if key in {"use_kpm", "use_nomount"} and effective_mode(inputs) == MODE_NO_KSU:
            continue
        if key == "use_kpm" and inputs.get("kernelsu_variant") == "Official":
            continue
        if key == "droidspaces_ntsync" and inputs.get("droidspaces", "off") == "off":
            continue
        if key == "ksu_mode":
            text = mode_label(inputs)
        elif key == "droidspaces":
            text = f"Droidspaces [{inputs.get(key, 'off')}]"
        elif key == "artifact_upload_mode":
            text = "Artifacts: " + ("đầy đủ" if inputs.get(key, "all") == "all" else "AnyKernel3")
        else:
            active = kpm_enabled(inputs) if key == "use_kpm" else enabled(inputs.get(key))
            text = f"{'✅' if active else '⬜'} {label}"
        buttons.append(
            InlineKeyboardButton(text, callback_data=f"bstog:{key}" if buildsave else cb)
        )
    return buttons


def _toggles_keyboard(
    inputs: dict, back_cb: str = "", selected_target: str = ""
) -> InlineKeyboardMarkup:
    buttons = _feature_buttons(inputs, selected_target)
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    nav = [InlineKeyboardButton("⬅️", callback_data=back_cb)] if back_cb else []
    nav.extend(
        [
            InlineKeyboardButton("❌", callback_data="gki:cancel"),
            InlineKeyboardButton("➡️", callback_data="gkitog:next"),
        ]
    )
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def _ensure_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    owner = context.user_data.get("gki_owner")

    message = update.effective_message
    if (
        owner == uid
        and message
        and message.message_id == context.user_data.get("gki_bot_msg_id")
        and message.chat_id == context.user_data.get("gki_chat_id")
    ):
        return True

    try:
        await update.callback_query.answer(
            "Phiên này không thuộc về bạn hoặc đã hết hạn.", show_alert=True
        )
    except Exception:
        pass
    return False


def _task_header(context) -> str:
    owner_name = context.user_data.get("gki_owner_name", "Unknown")
    owner_id = context.user_data.get("gki_owner", 0)
    return f'📋 <b>Task by <a href="tg://user?id={owner_id}">{html.escape(str(owner_name))}</a></b>\n┃\n┖ '


async def _safe_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _send_topic_safe(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, update: Update = None, **kwargs
):
    """Send to the current forum topic, retrying in the general chat if that topic is closed."""
    thread_id = None
    if update and update.effective_message and update.effective_message.is_topic_message:
        thread_id = update.effective_message.message_thread_id
    try:
        return await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            **kwargs,
        )
    except BadRequest as e:
        if "Topic_closed" not in str(e) or thread_id is None:
            raise
        return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


def _thread_id_from_update(update: Update):
    msg = update.effective_message if update else None
    return msg.message_thread_id if (msg and msg.is_topic_message) else None


async def _update_bot_msg(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    reply_markup=None,
    parse_mode=None,
    update: Update = None,
):
    """Edit tin nhắn bot đang track, hoặc gửi mới nếu chưa có."""
    bot_msg_id = context.user_data.get("gki_bot_msg_id")
    if bot_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except Exception:
            pass
    msg = await _send_topic_safe(
        context,
        chat_id,
        text,
        update=update,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    context.user_data["gki_bot_msg_id"] = msg.message_id
    context.user_data["gki_chat_id"] = msg.chat_id


def _cleanup(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gki", None)
    context.user_data.pop("gki_key", None)
    context.user_data.pop("gki_bot_msg_id", None)
    context.user_data.pop("gki_owner", None)
    context.user_data.pop("gki_owner_name", None)
    context.user_data.pop("gki_chat_id", None)


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
                    context.job_queue.run_once(
                        _del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id
                    )
                return ConversationHandler.END
            uses = await self.storage.get_uses(key)
            if uses <= 0:
                m = await update.message.reply_text("⚠️ Key không hợp lệ hoặc đã hết lượt.")
                if context.job_queue:
                    context.job_queue.run_once(
                        _del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id
                    )
                return ConversationHandler.END
            context.user_data["gki_key"] = key

        await _safe_delete(context, chat_id, update.message.message_id)

        context.user_data["gki_owner"] = update.effective_user.id
        context.user_data["gki_owner_name"] = update.effective_user.full_name
        context.user_data["gki"] = {
            "inputs": {
                "kernelsu_variant": "BakaSU",
                "version": "",
                "build_time": "Sun Dec 01 08:10:00 UTC 2024",
                "use_zram": False,
                "use_bbg": False,
                "use_kpm": "disabled (关闭)",
                "use_rekernel": False,
                "ksu_mode": MODE_NO_SUSFS,  # Giữ mặc định /gki: KSU bật, SUSFS tắt.
                "droidspaces": "off",
                "supp_op": False,
                "build_a12_5_10": False,
                "build_a13_5_15": False,
                "build_a14_6_1": False,
                "build_a15_6_6": False,
                "build_a16_6_12": False,
                "build_all": False,
                "release_type": "Actions",
                "artifact_upload_mode": "all",
                "sub_levels": "",
            }
        }

        header = _task_header(context)
        msg = await _send_topic_safe(
            context,
            chat_id,
            header + "Chọn KernelSU variant:",
            update=update,
            reply_markup=_kb_from_list("gkiksuvar", VARIANTS),
            parse_mode="HTML",
        )
        context.user_data["gki_bot_msg_id"] = msg.message_id
        context.user_data["gki_chat_id"] = msg.chat_id
        return GKI_KSU_VARIANT

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query and not await _ensure_owner(update, context):
            return ConversationHandler.END
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("❌ Đã huỷ phiên.")

        if context.job_queue:
            context.job_queue.run_once(
                _del_msg_job, when=60, chat_id=q.message.chat_id, data=q.message.message_id
            )

        _cleanup(context)
        return ConversationHandler.END

    async def timeout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        mention = (
            f'<a href="tg://user?id={user.id}">{html.escape(user.full_name)}</a>' if user else "Bạn"
        )
        notice = f"<b><blockquote>⏳ {mention}, phiên /gki đã hết hạn sau 60s.</blockquote></b>"
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
                await q.edit_message_text(notice, parse_mode="HTML")
            except Exception:
                pass
        elif update.effective_message:
            try:
                m = await update.effective_message.reply_text(notice, parse_mode="HTML")
                timeout_chat_id = m.chat_id
                timeout_message_id = m.message_id
            except Exception:
                pass

        if context.job_queue and timeout_chat_id and timeout_message_id:
            context.job_queue.run_once(
                _del_msg_job, when=60, chat_id=timeout_chat_id, data=timeout_message_id
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
                parse_mode="HTML",
            )
            return GKI_KSU_VARIANT

        if target in {"ksu_mode", "ksu_branch"}:
            await q.edit_message_text(
                header + "Chọn chế độ KSU/SUSFS (branch tự động theo upstream):",
                reply_markup=_mode_keyboard(),
                parse_mode="HTML",
            )
            return GKI_KSU_MODE

        if target == "version":
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("⏭️ Dùng mặc định", callback_data="gkiver:none")],
                    [
                        InlineKeyboardButton("⬅️", callback_data="gkiback:ksu_mode"),
                        InlineKeyboardButton("❌", callback_data="gki:cancel"),
                    ],
                ]
            )
            await q.edit_message_text(
                header
                + "⏭️ Nhập <code>tên version</code>.\nVD: nhập <code>Jinmups</code> thì tên kernel sẽ có dạng:\n<blockquote><code>5.10.149-Jinmups</code></blockquote>\nHoặc bấm nút để bỏ qua.",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return GKI_VERSION

        if target == "target":
            await q.edit_message_text(
                header + "Chọn phiên bản Android để build:",
                reply_markup=_build_target_keyboard(back_cb="gkiback:version"),
                parse_mode="HTML",
            )
            return GKI_BUILD_TARGET

        if target == "sub":
            inputs = context.user_data["gki"]["inputs"]
            target_key = context.user_data["gki"].get("selected_target")
            if not target_key:
                target_key = next(
                    (k for _, k in BUILD_TARGETS if inputs.get(k)), BUILD_TARGETS[0][1]
                )
                context.user_data["gki"]["selected_target"] = target_key

            available = SUB_LEVELS.get(target_key, [])
            selected = context.user_data["gki"].get("selected_subs")
            if not isinstance(selected, set):
                subs_raw = (inputs.get("sub_levels") or "").strip()
                if not subs_raw:
                    selected = set(available)
                else:
                    selected = {
                        sv for sv in [x.strip() for x in subs_raw.split(",")] if sv in available
                    }
                context.user_data["gki"]["selected_subs"] = selected

            target_label = next(
                (label for label, k in BUILD_TARGETS if k == target_key), target_key
            )
            count = len(selected)
            total = len(available)
            user_is_admin = await is_admin(update.effective_user.id, self.storage)
            kb = self._sub_version_keyboard(context, user_is_admin)
            await q.edit_message_text(
                header + f"Chọn sub-version cho <b>{target_label}</b>:\n"
                f"<i>(Đã chọn: {count}/{total})</i>",
                reply_markup=kb,
                parse_mode="HTML",
            )
            return GKI_SUB_VERSION

        if target == "toggles":
            inputs = context.user_data["gki"]["inputs"]
            selected_target = context.user_data["gki"].get("selected_target", "")
            await q.edit_message_text(
                header + "<b>Tùy chỉnh tính năng:</b>",
                reply_markup=_toggles_keyboard(
                    inputs, back_cb="gkiback:sub", selected_target=selected_target
                ),
                parse_mode="HTML",
            )
            return GKI_TOGGLES

        if target == "release":
            await q.edit_message_text(
                header + "Chọn loại release: (Nên chọn Actions)",
                reply_markup=_kb_from_list("gkirel", RELEASE_TYPES, back_cb="gkiback:toggles"),
                parse_mode="HTML",
            )
            return GKI_RELEASE_TYPE

        await q.answer("Không thể quay lại bước này.", show_alert=True)
        return ConversationHandler.END

    # === VARIANT ===
    async def set_variant(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return GKI_KSU_VARIANT
        q = update.callback_query
        await q.answer()
        _, val = q.data.split(":", 1)
        if val == "ReSukiSU":
            val = "BakaSU"
        if val not in VARIANTS:
            await q.answer("Biến thể không còn hỗ trợ. Mở lại /gki để chọn.", show_alert=True)
            return GKI_KSU_VARIANT
        context.user_data["gki"]["inputs"]["kernelsu_variant"] = val
        await q.edit_message_text(
            _task_header(context) + "Chọn chế độ KSU/SUSFS (branch tự động theo upstream):",
            reply_markup=_mode_keyboard(),
            parse_mode="HTML",
        )
        return GKI_KSU_MODE

    async def set_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return GKI_KSU_MODE
        q = update.callback_query
        await q.answer()
        value = q.data.split(":", 1)[1]
        if value not in MODE_CALLBACKS:
            return GKI_KSU_MODE
        inputs = context.user_data["gki"]["inputs"]
        inputs["ksu_mode"] = MODE_CALLBACKS[value]
        inputs.pop("cancel_susfs", None)
        inputs.update(normalize_inputs(inputs))
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⏭️ Dùng mặc định", callback_data="gkiver:none")],
                [
                    InlineKeyboardButton("⬅️", callback_data="gkiback:ksu_mode"),
                    InlineKeyboardButton("❌", callback_data="gki:cancel"),
                ],
            ]
        )
        await q.edit_message_text(
            _task_header(context)
            + "Nhập tên version (chữ ASCII, số, . _ + -; tối đa 64 ký tự), hoặc dùng mặc định. Branch được workflow tự chọn.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return GKI_VERSION

    # === VERSION (text input or button) ===
    async def set_version(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if update.callback_query:
            if not await _ensure_owner(update, context):
                return GKI_VERSION
            q = update.callback_query
            await q.answer()
            context.user_data["gki"]["inputs"]["version"] = ""
        else:
            if chat_id != context.user_data.get("gki_chat_id"):
                return GKI_VERSION
            txt = (update.message.text or "").strip()
            if txt.lower() == "none":
                val = ""
            else:
                val = txt if txt.startswith("-") else f"-{txt}"
            try:
                normalize_inputs({**context.user_data["gki"]["inputs"], "version": val})
            except ValueError as error:
                await update.message.reply_text(str(error))
                return GKI_VERSION
            context.user_data["gki"]["inputs"]["version"] = val
            await _safe_delete(context, chat_id, update.message.message_id)

        header = _task_header(context)
        await _update_bot_msg(
            context,
            chat_id,
            header + "Chọn phiên bản Android để build:",
            reply_markup=_build_target_keyboard(back_cb="gkiback:version"),
            parse_mode="HTML",
        )
        return GKI_BUILD_TARGET

    # === TOGGLES COMBINED ===
    async def toggle_feature(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return GKI_TOGGLES
        q = update.callback_query
        await q.answer()
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
                    parse_mode="HTML",
                )
                return GKI_RELEASE_TYPE
            else:
                # User: mặc định Actions, chuyển thẳng sang confirm
                context.user_data["gki"]["inputs"]["release_type"] = "Actions"
                return await self.confirm(q, context)

        toggle_map = {cb.split(":", 1)[1]: input_key for _, input_key, cb in TOGGLE_FEATURES}
        input_key = toggle_map.get(key)
        if input_key:
            cycle_feature(inputs, input_key, context.user_data["gki"].get("selected_target", ""))

        header = _task_header(context)
        selected_target = context.user_data["gki"].get("selected_target", "")
        await q.edit_message_text(
            header + "<b>Tùy chỉnh tính năng:</b>",
            reply_markup=_toggles_keyboard(
                inputs, back_cb="gkiback:sub", selected_target=selected_target
            ),
            parse_mode="HTML",
        )
        return GKI_TOGGLES

    # === BUILD TARGET (single-select) ===
    async def set_build_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return GKI_BUILD_TARGET
        q = update.callback_query
        await q.answer()
        _, key = q.data.split(":", 1)

        inputs = context.user_data["gki"]["inputs"]
        # Tắt hết rồi chỉ bật cái được chọn
        for _, k in BUILD_TARGETS:
            inputs[k] = k == key

        # Lưu target key và khởi tạo selected sub-versions = all
        context.user_data["gki"]["selected_target"] = key
        context.user_data["gki"]["selected_subs"] = set()  # mặc định không chọn gì

        user_is_admin = await is_admin(update.effective_user.id, self.storage)

        # Hiển thị chọn sub-version
        header = _task_header(context)
        target_label = next((label for label, k in BUILD_TARGETS if k == key), key)
        kb = self._sub_version_keyboard(context, user_is_admin)

        info_text = (
            "<i>(Bấm để chọn, sẽ tự động chuyển tiếp)</i>"
            if not user_is_admin
            else "<i>(Bấm để bật/tắt, ✅ = sẽ build)</i>"
        )

        await q.edit_message_text(
            header + f"Chọn sub-version cho <b>{target_label}</b>:\n{info_text}",
            reply_markup=kb,
            parse_mode="HTML",
        )
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
        if not await _ensure_owner(update, context):
            return GKI_SUB_VERSION
        q = update.callback_query
        await q.answer()
        _, val = q.data.split(":", 1)

        user_is_admin = await is_admin(update.effective_user.id, self.storage)

        target_key = context.user_data["gki"]["selected_target"]
        available = SUB_LEVELS.get(target_key, [])
        selected = context.user_data["gki"]["selected_subs"]

        if val not in {"done", "all", *available} or (not user_is_admin and val in {"done", "all"}):
            return GKI_SUB_VERSION
        if not user_is_admin and val not in ("done", "all"):
            # User: chọn 1 sub → vào toggles để chọn tính năng trước khi confirm
            context.user_data["gki"]["selected_subs"] = {val}
            context.user_data["gki"]["inputs"]["sub_levels"] = str(val)
            header = _task_header(context)
            inputs = context.user_data["gki"]["inputs"]
            selected_target = context.user_data["gki"].get("selected_target", "")
            await q.edit_message_text(
                header + "<b>Tùy chỉnh tính năng:</b>",
                reply_markup=_toggles_keyboard(
                    inputs, back_cb="gkiback:sub", selected_target=selected_target
                ),
                parse_mode="HTML",
            )
            return GKI_TOGGLES

        if val == "done":
            # Validate at least 1 selected
            if not selected:
                await q.answer("⚠️ Chọn ít nhất 1 sub-version!", show_alert=True)
                return GKI_SUB_VERSION
            # Explicit selection also includes catalog rows newer than upstream's default matrix.
            context.user_data["gki"]["inputs"]["sub_levels"] = ",".join(
                sub for sub in available if sub in selected
            )
            # Admin: đã chọn sub xong → vào Toggles
            header = _task_header(context)
            inputs = context.user_data["gki"]["inputs"]
            selected_target = context.user_data["gki"].get("selected_target", "")
            await q.edit_message_text(
                header + "<b>Tùy chỉnh tính năng:</b>",
                reply_markup=_toggles_keyboard(
                    inputs, back_cb="gkiback:sub", selected_target=selected_target
                ),
                parse_mode="HTML",
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
            reply_markup=kb,
            parse_mode="HTML",
        )
        return GKI_SUB_VERSION

    # === RELEASE TYPE ===
    async def set_release_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return GKI_RELEASE_TYPE
        q = update.callback_query
        await q.answer()
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
            if inputs.get("build_a12_5_10"):
                targets.append("Android 12 (5.10)")
            if inputs.get("build_a13_5_15"):
                targets.append("Android 13 (5.15)")
            if inputs.get("build_a14_6_1"):
                targets.append("Android 14 (6.1)")
            if inputs.get("build_a15_6_6"):
                targets.append("Android 15 (6.6)")
            if inputs.get("build_a16_6_12"):
                targets.append("Android 16 (6.12)")

        inputs = normalize_inputs(inputs, context.user_data["gki"].get("selected_target", ""))

        rt_map = {"Actions": "GitHub Actions", "Pre-Release": "Pre-Release", "Release": "Release"}
        release = rt_map.get(inputs.get("release_type", "Actions"), inputs.get("release_type", ""))

        subs = inputs.get("sub_levels", "")
        subs_display = "Tất cả" if not subs or str(subs).lower() in ("all", "*", "") else str(subs)

        ver = str(inputs.get("version", "")).strip("-").strip()
        ver_display = html.escape(ver) if ver else "(mặc định)"

        def flag(val):
            return "✅ Bật" if val else "❌ Tắt"

        target_key = context.user_data["gki"].get("selected_target", "")
        show_supp_op = target_key in SUPP_OP_TARGETS
        lines = [
            f"• Kernel Build: <b>{inputs.get('kernelsu_variant', '-')}</b>",
            f"• Chế độ: {mode_label(inputs)} (branch tự động)",
            f"• Custom version: {ver_display}",
            f"• KernelSU version: {', '.join(targets) if targets else '(chưa chọn)'}",
            f"• Sub-level: {subs_display}",
            f"• ZRAM: {flag(inputs.get('use_zram', True))}",
            f"• BBG: {flag(inputs.get('use_bbg', True))}",
            f"• KPM: {flag(kpm_enabled(inputs))}",
            f"• Re-Kernel: {flag(inputs.get('use_rekernel', False))}",
            f"• NoMount: {flag(inputs.get('use_nomount'))}",
            f"• Networking: {flag(inputs.get('use_networking'))}",
            f"• CVE-2026-43499: {flag(inputs.get('cve_2026_43499_patch'))}",
            f"• NTSync: {flag(inputs.get('droidspaces_ntsync'))}",
            f"• Xuất patch SUSFS: {flag(inputs.get('export_susfs_patches'))}",
            f"• Artifacts: {inputs.get('artifact_upload_mode', 'all')}",
            f"• Droidspaces: <b>{inputs.get('droidspaces', 'off')}</b>",
        ]
        if show_supp_op:
            lines.append(f"• OnePlus 8E: {flag(inputs.get('supp_op', False))}")
        lines.append(f"• Loại release: {release}")
        pretty = "\n".join(lines)

        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Xác nhận", callback_data="gkiconfirm")],
                [
                    InlineKeyboardButton("⬅️", callback_data=back_data),
                    InlineKeyboardButton("❌", callback_data="gki:cancel"),
                ],
            ]
        )
        header = _task_header(context)
        await q.edit_message_text(
            header + f"<b>Xác nhận build GKI</b>\n\n{pretty}", reply_markup=kb, parse_mode="HTML"
        )
        return GKI_CONFIRM

    # === DISPATCH ===
    async def do_dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await _ensure_owner(update, context):
            return GKI_CONFIRM
        q = update.callback_query
        await q.answer()
        inputs = context.user_data["gki"]["inputs"].copy()
        user = update.effective_user
        key = context.user_data.get("gki_key")
        user_is_admin = await is_admin(user.id, self.storage)

        if not user_is_admin:
            uses = await self.storage.get_uses(key or "")
            if not key or uses <= 0:
                m = await q.edit_message_text("❌ Key không hợp lệ hoặc hết lượt.")
                if context.job_queue:
                    context.job_queue.run_once(
                        _del_msg_job, when=60, chat_id=m.chat_id, data=m.message_id
                    )
                _cleanup(context)
                return ConversationHandler.END

        # Lấy workflow file từ config
        wf_files = list(self.config.GKI_WORKFLOWS.values())
        wf = wf_files[0] if wf_files else "main.yml"

        try:
            inputs, dispatch_file, dispatch_inputs = prepare_dispatch(inputs, wf)
        except ValueError as error:
            await q.edit_message_text(f"❌ {error}\nMở lại /gki để chọn cấu hình.")
            _cleanup(context)
            return ConversationHandler.END

        job = {
            "type": "gki",
            "repo": self.config.GKI_REPO,
            "workflow_file": dispatch_file,
            "ref": self.config.GKI_DEFAULT_BRANCH,
            "inputs": inputs,
            "dispatch_inputs": dispatch_inputs,
            "user_id": user.id,
            "user_name": user.full_name,
            "chat_id": update.effective_chat.id,
            "message_thread_id": _thread_id_from_update(update),
        }
        request_id = f"gki:{q.message.chat_id}:{q.message.message_id}:{user.id}"
        try:
            saved = await self.storage.enqueue_job(
                job, key=None if user_is_admin else key, request_id=request_id
            )
        except ValueError as error:
            _cleanup(context)
            await q.edit_message_text(f"❌ {error}")
            return ConversationHandler.END
        _cleanup(context)
        await q.edit_message_text(
            f"✅ Đã xếp hàng build GKI #{saved['_id']}.\n"
            "Bot sẽ gửi GitHub khi có lượt trống và thông báo khi hoàn tất.\n"
            "Lượt key được giữ trước; sẽ hoàn nếu GitHub xác nhận từ chối yêu cầu.",
        )
        return ConversationHandler.END


def build_gki_conversation(gh, storage, config):
    flow = GKIFlow(gh, storage, config)
    cancel_handler = CallbackQueryHandler(flow.cancel, pattern=r"^gki:cancel$")
    back_handler = CallbackQueryHandler(flow.back, pattern=r"^gkiback:.+$")
    return ConversationHandler(
        entry_points=[CommandHandler("gki", flow.start)],
        states={
            GKI_KSU_VARIANT: [
                CallbackQueryHandler(flow.set_variant, pattern=r"^gkiksuvar:.+"),
                back_handler,
                cancel_handler,
            ],
            GKI_KSU_MODE: [
                CallbackQueryHandler(flow.set_mode, pattern=r"^gkimode:.+"),
                back_handler,
                cancel_handler,
            ],
            GKI_VERSION: [
                CallbackQueryHandler(flow.set_version, pattern=r"^gkiver:none$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, flow.set_version),
                back_handler,
                cancel_handler,
            ],
            GKI_TOGGLES: [
                CallbackQueryHandler(flow.toggle_feature, pattern=r"^gkitog:.+$"),
                back_handler,
                cancel_handler,
            ],
            GKI_BUILD_TARGET: [
                CallbackQueryHandler(flow.set_build_target, pattern=r"^gkitgt:.+"),
                back_handler,
                cancel_handler,
            ],
            GKI_SUB_VERSION: [
                CallbackQueryHandler(flow.toggle_sub_version, pattern=r"^gkisub:.+"),
                back_handler,
                cancel_handler,
            ],
            GKI_RELEASE_TYPE: [
                CallbackQueryHandler(flow.set_release_type, pattern=r"^gkirel:.+"),
                back_handler,
                cancel_handler,
            ],
            GKI_CONFIRM: [
                CallbackQueryHandler(flow.do_dispatch, pattern=r"^gkiconfirm$"),
                back_handler,
                cancel_handler,
            ],
            ConversationHandler.TIMEOUT: [
                CallbackQueryHandler(flow.timeout),
                MessageHandler(filters.ALL, flow.timeout),
            ],
        },
        fallbacks=[cancel_handler],
        allow_reentry=True,
        name="gki_conversation",
        persistent=False,
        conversation_timeout=60,  # 60s → tự dọn state treo
    )
