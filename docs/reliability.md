# Vận hành bản có hàng đợi và worker

## Nâng cấp VPS đang chạy

Backup `.env`, state theo `STORAGE_PATH` và `web/data/` trước khi cập nhật source. Sau khi đưa code mới lên VPS:

```bash
bash start.sh
bash run.sh health
bash run.sh logs
```

Phải cài lại dependency qua `start.sh`, không chỉ restart: bản này thay Motor bằng PyMongo Async. Không chép `.env.example` đè `.env` hiện tại. Mọi cấu hình mới đều có mặc định; không thay bot token, MongoDB URI hay đường dẫn state đang dùng. Chỉ chạy một instance cho mỗi token/state. Chưa có build thực tế với token của bạn trong kiểm thử source; sau nâng cấp nên chạy một build nhỏ và kiểm tra thông báo group/DM cùng dashboard.

Không chạy đồng thời code cũ và mới. Nếu cần rollback, dừng bot, kiểm tra hàng đợi/outbox trước và khôi phục cả code lẫn snapshot phù hợp; bản cũ không hiểu lượt key đang giữ và job `dispatching` của bản mới.

## Hàng đợi và key

Job được lưu trước khi gửi mạng. Mặc định tối đa 10 job đã gửi/đang chạy của bot (`MAX_ACTIVE_BUILDS`); phần dư chờ local, tối đa 500 job chờ. Batch buildsave chỉ gửi một job tại một thời điểm. Giới hạn này không đếm run tạo thủ công hoặc bot khác, không điều khiển concurrency/cancel-in-progress của workflow GitHub. Nếu workflow chỉ cho phép một run, đặt `MAX_ACTIVE_BUILDS=1` và kiểm tra workflow riêng.

Key thường: tối đa một job chưa xong mỗi user; key VIP bỏ giới hạn đó nhưng vẫn trừ lượt. Giữ lượt và tạo job là một thao tác atomic. Hủy khi còn chờ hoặc GitHub xác nhận từ chối dispatch sẽ hoàn lượt một lần. Khi GitHub đã nhận, lượt được dùng; build thất bại/hủy sau đó không tự hoàn. Key owner đã xóa sẽ không tự được tạo lại khi refund.

`/jobs` hiển thị ID nội bộ và run ID nếu có; user chỉ thấy job của mình, admin thấy toàn bộ. `/canceljob ID` hủy job thuộc quyền quản lý: còn local thì hủy/hoàn lượt ngay; đang gửi/chạy thì lưu yêu cầu và đối chiếu GitHub để hủy. Các lệnh hủy batch cũ vẫn hoạt động.

Nếu mất phản hồi dispatch, bot không gửi lại mù và không tự hoàn lượt. Sau 20 phút chưa xác định được run (khi API kiểm tra hoạt động), bot báo owner. Admin kiểm tra repository, workflow, branch và thời điểm gửi trên GitHub, sau đó chọn đúng một cách:

```text
/resolvejob 42 123456789
/resolvejob 42 refund
```

Lệnh đầu liên kết run ID sau khi kiểm tra workflow/branch và không cho gán run đã thuộc job khác. Lệnh refund chỉ áp dụng job đã chuyển `completed/unknown`, chưa có run ID, sau khi chắc chắn GitHub không tạo build. Không refund chỉ vì API đang lỗi. Job chưa rõ kết quả/lượt còn giữ được bảo vệ khỏi dọn lịch sử tự động.

## Health và phục hồi

```bash
bash run.sh status
bash run.sh health
curl -i http://127.0.0.1:5000/healthz
```

PM2 status chỉ xác nhận process. `/healthz` trả 200 khi dispatcher, poller, notification worker và storage khỏe; trả 503 nếu chưa sẵn sàng hoặc một phần quan trọng lỗi/treo. Response có tuổi heartbeat, số lần phục hồi, thời điểm API/backup gần nhất, lỗi cloud dạng loại lỗi; không chứa token/key. Worker phụ vẫn hiển thị trạng thái riêng, nhưng lỗi backup/cloud/cache không nhất thiết làm toàn bộ bot ngừng phục vụ. Đây là health nội bộ, không bảo đảm Telegram/GitHub đang truy cập được; xem thêm thời điểm success và log.

Worker crash hoặc quá `WORKER_STALE_SECONDS` (600 giây mặc định) sẽ bị hủy và thử phục hồi tối đa 3 lần/giờ, có backoff; sau đó báo owner, không quay vòng vô hạn. Supervisor không thay PM2. PM2 còn restart process khi crash hoặc vượt ngưỡng RAM 400 MB. Khi nhận cảnh báo, xem nguyên nhân trong log trước khi `bash run.sh restart`.

Poller mặc định 30 giây. GitHub cache dùng chung, hết hạn sau hai chu kỳ; trình duyệt chỉ đọc snapshot, không tạo thêm API request. Broadcast chạy worker riêng để không giữ handler chờ gửi cho toàn bộ user. RetryAfter, user chặn bot, lỗi từng nhóm không làm hỏng thông báo cho nơi nhận khác. Số lần thử gửi mặc định tối đa 8; trạng thái giao nhận thất bại vẫn có trong outbox local. Outbox đã kết thúc được giữ 7 ngày; tin còn pending được giữ lại.

Không cam kết không trùng tuyệt đối: Telegram có thể nhận tin trước khi bot kịp ghi xác nhận rồi crash, dẫn tới gửi lại sau restart. Với GitHub, ưu tiên run ID trả trực tiếp; đối chiếu fallback theo thời gian vẫn cần owner kiểm tra khi không chắc chắn.

## Backup và bảo mật

Backup ZIP được tạo lúc worker khởi động rồi mỗi 24 giờ, giữ 14 bản đã kiểm tra tại `data/backups/`. Chỉ xóa các bản vượt retention sau khi bản mới hợp lệ. Có thể đổi `BACKUP_DIR`, `BACKUP_INTERVAL_HOURS`, `BACKUP_KEEP` (ít nhất 2). Backup không chứa `.env` nhưng chứa key, user/chat ID, nội dung broadcast và lịch sử, nên vẫn là dữ liệu riêng tư.

```bash
bash run.sh backup
bash run.sh backups
venv/bin/python tools/manage_backups.py verify /path/to/kernel-backup-....zip
```

Lệnh `verify` chỉ đọc/kiểm tra, không khôi phục hay giải nén. Khôi phục ZIP qua `/data` bằng tài khoản có quyền quản trị; bot kiểm tra dữ liệu/đường dẫn trước khi thay state/catalog và giữ state cũ trong `.bak`. Không chỉnh tay state khi bot đang chạy. Không dùng bản backup từ người không tin cậy.

Không đặt backup trong `web/` (config chặn), thư mục public khác hoặc commit lên Git. Tự sao lưu `.env` bằng kênh an toàn và chuyển backup ra ngoài VPS; backup local/MongoDB snapshot không bảo vệ đầy đủ khi mất cả máy hoặc xóa nhầm được đồng bộ lên cloud. MongoDB push nền có thể trễ tối đa một chu kỳ cộng thời gian mạng khi hoạt động bình thường; local state mới là dữ liệu vận hành chính. Giữ dung lượng đĩa trống và theo dõi log backup.
