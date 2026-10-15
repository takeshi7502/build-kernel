# Kiến trúc và cấu hình

## Luồng hoạt động

1. Bot nhận lệnh bằng Telegram long polling và kiểm tra quyền/hạn mức.
2. Hội thoại GKI/OKI hoặc batch lưu job vào hàng đợi local. Kiểm tra/giữ lượt key và lưu job trong cùng một lần ghi; callback lặp lại không tạo thêm build.
3. Dispatcher ghi trạng thái `dispatching` trước khi gọi GitHub. Yêu cầu `return_run_details=true` cho phép lưu trực tiếp `workflow_run_id` khi GitHub trả HTTP 200; vẫn hỗ trợ phản hồi 204 cũ.
4. Poller cập nhật tiến trình/kết quả; ghi việc cần cập nhật catalog và gửi thông báo vào state trước. Worker catalog và outbox xử lý riêng, không phụ thuộc Telegram đang sẵn sàng.
5. HTTP server `aiohttp` cùng process phục vụ dashboard/API `/api/data` từ cache chung và `/healthz` từ heartbeat/storage. Mỗi lượt xem dashboard không gọi thêm GitHub.
6. Supervisor giám sát worker, tự phục hồi có giới hạn; worker backup và cloud sync chạy nền, không giữ khóa ghi state trong lúc chờ MongoDB.

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
| Điều phối | `MAX_ACTIVE_BUILDS`, `POLL_INTERVAL`, `WORKER_STALE_SECONDS` | Giới hạn job của bot và nhịp kiểm tra |
| Thông báo | `NOTIFICATION_MAX_ATTEMPTS` | Số lần thử gửi tối đa mỗi nơi nhận |
| Sao lưu | `BACKUP_DIR`, `BACKUP_INTERVAL_HOURS`, `BACKUP_KEEP`, `CLOUD_SYNC_INTERVAL` | Backup local có phiên bản và cloud snapshot |

`WEB_HOST` là địa chỉ lắng nghe của backend; `DASHBOARD_URL` chỉ là link công khai trong thông báo. Với Nginx cùng VPS, giữ `WEB_HOST=127.0.0.1`; Nginx chuyển tiếp tới `WEB_PORT`.

## Quy tắc lưu trữ

Giữ một bot process cho một state và token. `storage.py` ghi JSON qua file tạm, `fsync` rồi thay thế nguyên tử để tránh file dở dang. File vẫn là nguồn chính, có cache kiểm tra fingerprint và khóa thao tác; ghi đĩa chạy qua thread để giảm chặn event loop. Khi hủy worker, khóa chỉ được nhả sau khi thread ghi xong. Không tự sửa `data.json` khi bot đang chạy; dừng bot và backup trước khi bảo trì thủ công. Không tự đổi sang SQLite ở bản nâng cấp này để giữ tương thích dữ liệu cũ; JSON phù hợp một instance với dữ liệu vừa phải, không phải database cho nhiều writer.

`MONGODB_SYNC_MODE` có các chế độ:

- `off`: chỉ dùng local JSON.
- `pull`: nạp snapshot cloud lúc khởi tạo và thay state local nếu có snapshot; không đẩy thay đổi ngược lên cloud.
- `push`: giữ state local hiện có làm nguồn chính; nếu đây là state mới thì có thể khôi phục cloud, sau đó cho phép đẩy state local lên cloud.
- `auto`: chọn theo môi trường hệ điều hành như mô tả trong `.env.example`.

`MONGODB_SYNC_WRITER_HOSTNAME` giới hạn máy được phép ghi cloud. Không coi đồng bộ cloud là cơ chế hòa giải thay đổi của nhiều máy; dùng một writer cho production. Copy state production sang máy dev thì nên dùng `off` hoặc `pull`.

MongoDB dùng `pymongo.AsyncMongoClient`; thay đổi local được gộp thành snapshot nền, mặc định mỗi 30 giây và có lần flush giới hạn thời gian khi dừng. Cloud có thể trễ so với đĩa local. Snapshot mới phát sinh khi đang push sẽ được giữ dirty cho lần tiếp theo; lỗi cloud không khóa các lệnh bot.

Nếu chưa có state local mà MongoDB được cấu hình nhưng không truy cập được, bot dừng khởi tạo để tránh tạo state mới đè lên backup cũ. Nếu đã có state local hợp lệ, bot có thể tiếp tục chạy local và ghi lỗi backup vào log.

## Phân chia module

Các module `gki.py`, `oki.py`, `buildsave.py` phụ trách hội thoại/input. `dispatch.py` gửi job đã lưu; `notifications.py` gửi outbox/broadcast; `status.py` giữ cache GitHub; `workers.py` giám sát heartbeat. `permissions.py` phân quyền; `storage.py` lưu state; `web_sync.py` dựng dữ liệu dashboard; `catalog.py` cập nhật link tải; `backups.py` kiểm tra ZIP; `maintenance.py` chạy bảo trì định kỳ; `github.py` đóng gói API client. `main.py` ghép handler, poller, vòng đời ứng dụng và HTTP server.

Bot ưu tiên run ID trả trực tiếp theo [GitHub REST API](https://docs.github.com/en/rest/actions/workflows?apiVersion=2022-11-28#create-a-workflow-dispatch-event). Chỉ khi không có ID mới đối chiếu workflow, branch, thời điểm thực sự gửi (không phải thời gian xếp hàng) và run chưa gán. Nhiều ứng viên thì không đoán. HTTP timeout/5xx không được tự gửi lại vì có thể GitHub đã nhận build. Sau 20 phút kiểm tra thành công mà chưa xác định được run, job chuyển `unknown`, giữ lượt đang treo và báo owner dùng `/resolvejob` để liên kết run hoặc hoàn lượt sau khi kiểm tra. Fallback theo thời gian vẫn có giới hạn; muốn chắc chắn cả khi mất phản hồi cần mã request riêng trong workflow bên repository kernel.

Outbox tồn tại qua restart, retry có backoff và giới hạn số lần, độc lập cho group và DM. Telegram không cung cấp giao dịch chung với storage local: nếu đã gửi thành công rồi process chết trước khi lưu xác nhận, thông báo có thể lặp. Đây là giao nhận kiểu at-least-once, không phải exactly-once. Chi tiết thao tác và giới hạn ở [vận hành](reliability.md).

`tools/` chứa chương trình chỉ chạy khi bảo trì. Không import công cụ này vào bot và không thêm chúng vào PM2. Công cụ chỉnh workflow kernel tác động repository build khác với repository chứa bot.
