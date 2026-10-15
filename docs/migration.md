# Nâng cấp từ bản có userbot

Bản mới chỉ duy trì bot Telegram thông thường. Đọc file này nếu VPS cũ có PM2 process `gki-userbot` hoặc trước đây chạy `bot/userbot.py`.

1. Ghi lại đường dẫn repository trên VPS và backup `.env`, `data.json`, `bot/data.json` nếu có, cùng `web/data/`. Không ghi đè các file này bằng bản mẫu.
2. Dừng các process cũ trong thời gian cập nhật để chúng không ghi state song song.
3. Cập nhật source. Nếu Git báo thay đổi local, xử lý thay đổi đó trước; không dùng reset cưỡng bức để bỏ qua.
4. Đối chiếu `.env` với `.env.example`. Giữ bot token/GitHub token hiện có; bỏ các biến `USERBOT_*`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_STRING_SESSION`, `TELEGRAM_SESSION` vì không còn được dùng.
5. Kiểm tra `STORAGE_PATH` trỏ đúng state cũ. Nếu trước đây lưu tại `bot/data.json`, đặt `STORAGE_PATH=bot/data.json`; không để mặc định tạo state rỗng ngoài ý muốn.
6. Xóa PM2 entry cũ bằng lệnh dưới rồi khởi chạy bot mới.

```bash
bash run.sh migrate
bash start.sh
bash run.sh status
bash run.sh logs
```

`migrate` chỉ dừng/xóa process PM2 tên `gki-userbot` và lưu danh sách PM2; không xóa dữ liệu hoặc file session. Nếu process cũ dùng tên khác hay chạy bằng systemd/screen, tự dừng đúng dịch vụ đó. Có thể xóa file session cá nhân sau khi đã tự xác nhận không còn sử dụng; chúng không còn là dependency của bản mới.

Chỉ để một `gki-bot` hoạt động với token đang dùng. Kiểm tra `/ping`, một lượt build thử, và dashboard trước khi bỏ backup. Các job cũ đã tạo bằng tài khoản cá nhân có thể mang ID chat mà Bot API không truy cập được; owner cần cho bot vào nhóm hoặc mở chat `/start` để nhận thông báo mới.

Các helper cũ được chuyển khỏi root:

| Đường dẫn cũ | Đường dẫn mới |
| --- | --- |
| `local_patch.py` | `tools/local_patch.py` |
| `sync_and_patch.py` | `tools/sync_and_patch.py` |
| `switch_runner.py` | `tools/switch_runner.py` |
| `setup_runners.sh` | `tools/setup_runners.sh` |
| `bot/recover_jobs.py` | `tools/recover_jobs.py` |

Helper nhận đường dẫn local clone qua tham số, không còn đường dẫn ổ đĩa Windows cố định. Công cụ patch mặc định xem trước; thêm `--apply` mới ghi. Đọc [hướng dẫn tools](../tools/README.md) trước khi dùng lại lệnh bảo trì cũ.
