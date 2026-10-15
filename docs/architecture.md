# Kiến trúc và cấu hình

## Luồng hoạt động

1. Bot nhận lệnh bằng Telegram long polling và kiểm tra quyền/hạn mức.
2. Hội thoại GKI/OKI hoặc batch tạo input và gửi yêu cầu workflow_dispatch tới GitHub Actions.
3. Job được lưu trong state local. Poller tìm run tương ứng, cập nhật trạng thái và quản lý batch đang chờ.
4. Khi workflow hoàn tất, bot thu thập kết quả, cập nhật catalog và gửi thông báo Telegram.
5. HTTP server `aiohttp` cùng process phục vụ dashboard và API `/api/data`.

Bot không compile kernel trên VPS. Self-hosted runner, nếu cài riêng, mới là nơi biên dịch. Vì bot và HTTP server cùng process nên restart `gki-bot` sẽ restart cả hai.

## Nhóm cấu hình

`.env` nằm tại gốc repository; `.env.example` là nguồn tham khảo đầy đủ. Các nhóm cần quan tâm:

| Nhóm | Biến tiêu biểu | Vai trò |
| --- | --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN`, `OWNER_ID`, `ADMIN_IDS` | Token và phân quyền |
| GitHub | `GITHUB_TOKEN`, `GITHUB_OWNER`, `UPSTREAM_OWNER` | Truy cập repository và nguồn upstream |
| GKI | `GKI_REPO`, `GKI_DEFAULT_BRANCH`, `GKI_WORKFLOWS` | Repository, branch và mapping workflow |
| OnePlus | `OKI_REPO`, `OKI_DEFAULT_BRANCH`, `OKI_WORKFLOW` | Cấu hình luồng `/oki` |
| Dữ liệu | `STORAGE_PATH`, `MONGODB_*` | State local và đồng bộ cloud |
| Dashboard | `WEB_HOST`, `WEB_PORT`, `DASHBOARD_URL` | Địa chỉ bind và URL công khai |

`WEB_HOST` là địa chỉ lắng nghe của backend; `DASHBOARD_URL` chỉ là link công khai trong thông báo. Với Nginx cùng VPS, giữ `WEB_HOST=127.0.0.1`; Nginx chuyển tiếp tới `WEB_PORT`.

## Quy tắc lưu trữ

Giữ một bot process cho một state và token. `storage.py` ghi JSON qua file tạm rồi thay thế nguyên tử để tránh file dở dang. Không tự sửa `data.json` khi bot đang chạy; dừng bot và backup trước khi bảo trì thủ công.

`MONGODB_SYNC_MODE` có các chế độ:

- `off`: chỉ dùng local JSON.
- `pull`: nạp snapshot cloud lúc khởi tạo và thay state local nếu có snapshot; không đẩy thay đổi ngược lên cloud.
- `push`: giữ state local hiện có làm nguồn chính; nếu đây là state mới thì có thể khôi phục cloud, sau đó cho phép đẩy state local lên cloud.
- `auto`: chọn theo môi trường hệ điều hành như mô tả trong `.env.example`.

`MONGODB_SYNC_WRITER_HOSTNAME` giới hạn máy được phép ghi cloud. Không coi đồng bộ cloud là cơ chế hòa giải thay đổi của nhiều máy; dùng một writer cho production. Copy state production sang máy dev thì nên dùng `off` hoặc `pull`.

Nếu chưa có state local mà MongoDB được cấu hình nhưng không truy cập được, bot dừng khởi tạo để tránh tạo state mới đè lên backup cũ. Nếu đã có state local hợp lệ, bot có thể tiếp tục chạy local và ghi lỗi backup vào log.

## Phân chia module

Các module `gki.py`, `oki.py`, `buildsave.py` phụ trách hội thoại và input build. `permissions.py` phụ trách phân quyền; `storage.py` phụ trách state; `web_sync.py` tạo dữ liệu realtime cho dashboard; `catalog.py` cập nhật catalog link tải; `backups.py` kiểm tra backup; `github.py` đóng gói GitHub API client. `main.py` ghép ứng dụng Telegram, GitHub, scheduler và HTTP server.

GitHub không trả run ID trong phản hồi dispatch hiện tại, nên bot ghép theo workflow, branch, thời điểm và các run chưa được gán. Đây vẫn là cách ghép dựa trên thời gian; muốn đối chiếu tuyệt đối cần thêm mã yêu cầu riêng vào workflow ở repository kernel.

`tools/` chứa chương trình chỉ chạy khi bảo trì. Không import công cụ này vào bot và không thêm chúng vào PM2. Công cụ chỉnh workflow kernel tác động repository build khác với repository chứa bot.
