with open('bot/main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Patch 1: update_batch_message - add all-done notification after edit_message_text
old_all_done = '''        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"update_batch_message error: {e}")'''

new_all_done = '''        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML"
        )
        
        # Nếu tất cả đã hoàn thành, gửi thêm 1 thông báo tóm tắt riêng
        if completed_count == total_count:
            success_count = sum(1 for j in jobs if j.get("conclusion") == "success")
            fail_count = total_count - success_count
            summary_lines = [f"✅ <b>Đã hoàn thành toàn bộ {total_count} bản build {variant}!</b>\\n"]
            for j in jobs:
                full_ver = j.get("bs_full_ver", "")
                conclusion = j.get("conclusion", "failure")
                icon = "✅" if conclusion == "success" else "❌"
                summary_lines.append(f"{icon} {variant} — {full_ver}")
            summary_lines.append(f"\\n<i>Thành công: {success_count} | Thất bại: {fail_count}</i>")
            summary_text = "\\n".join(summary_lines)
            
            final_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🌐 Web Dashboard", url="https://kernel.takeshi.dev/")]
            ])
            
            # Chỉ gửi 1 lần - check flag trong job đầu tiên
            if not first_job.get("batch_all_notified"):
                await storage.update_job(first_job["_id"], {"batch_all_notified": True})
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=summary_text,
                        reply_markup=final_markup,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"update_batch_message error: {e}")'''

if old_all_done in content:
    content = content.replace(old_all_done, new_all_done)
    with open('bot/main.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: main.py patched with all-done notification")
else:
    print("ERROR: old block not found")
    idx = content.find('update_batch_message error')
    print("Context:", repr(content[max(0,idx-300):idx+100]))
