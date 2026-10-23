# Kernel Build Telegram Bot

Bot Telegram quản lý build GKI/OnePlus bằng GitHub Actions và cung cấp dashboard theo dõi tiến trình. Kernel được biên dịch trên runner của GitHub Actions; máy chạy bot chỉ điều phối, lưu trạng thái và gửi thông báo.

Source hiện chỉ chạy một bot Telegram qua Bot API. Không cần tài khoản Telegram cá nhân, API ID/hash, string session hay tiến trình Telethon.

## Cấu trúc source

```text
bot/                         Backend Python
  __main__.py                Entry point: python -m bot
  config.py                  Đọc và kiểm tra cấu hình .env
  main.py                    Telegram handlers, poller, HTTP server
  github.py                  GitHub API client dùng chung
  dispatch.py                Hàng đợi bền vững, gửi build và lưu run ID
  workers.py                 Giám sát worker, heartbeat và tự phục hồi
  notifications.py           Outbox thông báo/broadcast, retry có giới hạn
  status.py                  Cache GitHub dùng chung cho poller và dashboard
  maintenance.py             Backup định kỳ, cloud sync và cập nhật catalog
  catalog.py                 Cập nhật link tải kernel, giữ catalog đã có
  backups.py                 Kiểm tra ZIP backup trước khi khôi phục
  gki.py                     Hội thoại /gki
  gki_contract.py            Chuẩn hóa input, tính năng và định tuyến workflow GKI
  gki_manifest.json          Danh mục phiên bản lấy từ repo build đã rà soát
  oki.py                     Hội thoại /oki
  buildsave.py               Hội thoại build hàng loạt
  permissions.py             Quyền owner/admin/member
  storage.py                 Local JSON và đồng bộ MongoDB tùy chọn
  web_sync.py                Tạo dữ liệu dashboard từ lịch sử build
deploy/                      Cấu hình chạy dịch vụ bằng PM2
scripts/                     Tự cấu hình domain, Nginx, HTTPS và .env
docs/                        Kiến trúc, vận hành và chuyển từ bản cũ
tools/                       Công cụ bảo trì tùy chọn; không chạy cùng bot
web/                         Dashboard HTML/JavaScript/SCSS, build bằng Webpack
  data/                      Catalog JSON do bot tạo/cập nhật
  dist/                      Bundle tự build, không theo dõi Git
tests/                       Kiểm tra local, không đẩy lên Git
.env.example                 Mẫu cấu hình có giải thích
requirements.txt             Dependency để chạy bot
start.sh                     Cài đặt/cập nhật dependency và khởi chạy
run.sh                       Thao tác hằng ngày
```

Đọc thêm [kiến trúc và dữ liệu](docs/architecture.md), [vận hành và xử lý sự cố](docs/reliability.md), [nâng cấp từ bản cũ](docs/migration.md) và [công cụ bảo trì](tools/README.md).

Bot đã chuyển sang [hợp đồng build GKI mới ngày 24/10/2026](docs/gki-build-contract.md). Cần cập nhật cả repo workflow trước khi chạy bot mới; không cần thêm biến `.env`.

## Cài mới trên Ubuntu/Debian

Chuẩn bị Python 3.10 trở lên, Node.js/npm phù hợp dependency trong `web/package.json`, Git và quyền `sudo` để cài package. Chạy bot bằng user thường, dùng cùng user đó cho mọi lệnh PM2.

```bash
git clone https://github.com/takeshi7502/build-kernel.git
cd build-kernel
cp .env.example .env
nano .env
```

Điền các giá trị bắt buộc:

| Biến | Nội dung |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token bot từ BotFather |
| `OWNER_ID` | ID Telegram dạng số của owner |
| `GITHUB_TOKEN` | Token có quyền truy cập repository và thao tác Actions cần dùng |
| `GITHUB_OWNER` | Owner/organization chứa repository build |
| `GKI_REPO` | Tên repository chứa workflow GKI |

Kiểm tra `GKI_DEFAULT_BRANCH`, `GKI_WORKFLOWS` và cấu hình `OKI_*` khớp repository thực tế. Token cần quyền Actions để chạy/xem/hủy workflow; các chức năng sửa file/sync fork còn cần quyền Contents tương ứng. Không dùng `GITHUB_RUNNER_TOKEN` thay cho `GITHUB_TOKEN`.

```bash
bash start.sh
bash run.sh status
bash run.sh logs
```

`start.sh` cài dependency còn thiếu trên Ubuntu/Debian, tạo `venv`, build frontend, kiểm tra cấu hình và chạy duy nhất PM2 process `gki-bot`. Thư mục dữ liệu có sẵn được giữ lại. Sau khi chạy ổn, bật khởi động cùng hệ thống theo lệnh PM2 hướng dẫn:

```bash
pm2 startup
# Thực hiện đúng lệnh sudo PM2 vừa in ra, sau đó:
pm2 save
```

Dashboard mặc định chỉ lắng nghe `127.0.0.1:5000`. Muốn xem từ máy cá nhân mà chưa có domain, dùng SSH tunnel:

```bash
ssh -L 5000:127.0.0.1:5000 ubuntu@IP_VPS
```

Sau đó mở `http://localhost:5000`. Để đưa web lên domain, trỏ DNS về đúng VPS và cho phép cổng 80/443 trong firewall/security group của nhà cung cấp. Nếu có nhiều bản ghi A/AAAA, mọi địa chỉ đều phải phục vụ đúng VPS; xóa bản ghi cũ. Nếu Cloudflare chặn/redirect ACME, tạm chuyển DNS only khi cấp chứng chỉ.

Bot đã cài rồi thì mở `bash run.sh`, chọn **7**, hoặc chạy trực tiếp:

```bash
bash run.sh web kernel.takeshi.dev
```

Cũng có thể nhập `https://kernel.takeshi.dev/`. Email gia hạn là tùy chọn. Script tự cài/bật Nginx và Certbot, kiểm tra ACME trên từng IP DNS, cấp SSL, chuyển HTTP sang HTTPS, bật gia hạn, lưu `DASHBOARD_URL` và `WEB_HOST=127.0.0.1` vào `.env`, rồi restart riêng bot và kiểm tra web. `.env` cũ được giữ tại `.env.bak`; cấu hình Nginx cũ được lưu trong `/etc/nginx/gki-bot-backups/`. Khi lỗi, script báo bước chưa xong và phục hồi cấu hình cũ nếu có.

Cài mới cả bot và HTTPS trong một lần:

```bash
bash start.sh --domain kernel.takeshi.dev
```

`bash start.sh` không tham số cũng có menu lựa chọn khi chạy trong terminal; `--ssl` vẫn được hỗ trợ. Script không tự thay DNS hay security group của nhà cung cấp vì không có quyền truy cập tài khoản đó. Dashboard hiển thị thông tin build; không đưa dữ liệu bí mật vào tên build.

## Chạy thủ công để phát triển

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cd web
npm ci
npm run build
cd ..
python -m bot
```

PowerShell kích hoạt môi trường bằng `.\venv\Scripts\Activate.ps1`; entry point vẫn là `python -m bot`. Chỉ chạy một instance cho mỗi bot token và file dữ liệu. Chạy đồng thời PM2 và một phiên thủ công sẽ gây xung đột Telegram polling.

## Vận hành

| Lệnh | Tác dụng |
| --- | --- |
| `bash run.sh` | Mở menu thao tác |
| `bash run.sh status` | Xem trạng thái process |
| `bash run.sh health` | Kiểm tra worker và storage qua `/healthz` |
| `bash run.sh backup` | Tạo ZIP backup local và kiểm tra nội dung |
| `bash run.sh backups` | Liệt kê các ZIP backup local |
| `bash run.sh logs` | Xem log trực tiếp; Ctrl+C để thoát |
| `bash run.sh restart` | Khởi động lại bot và nạp cấu hình mới |
| `bash run.sh update` | Cập nhật source bằng fast-forward, chưa restart |
| `bash run.sh deploy` | Cập nhật source, cài dependency, build dashboard và restart |
| `bash run.sh web [domain]` | Tự cấu hình domain, Nginx, SSL và link dashboard |

Script update không ghi đè thay đổi local. Nếu Git báo worktree chưa sạch, kiểm tra và lưu thay đổi trước khi cập nhật. Khi sửa `.env`, dùng `restart`; khi dependency/frontend thay đổi, dùng `start.sh` hoặc `deploy`.

Sao lưu `.env`, `data.json` (hoặc đường dẫn `STORAGE_PATH`) và `web/data/` trước khi nâng cấp. Không commit `.env`, token, dữ liệu runtime hay file session cũ.

## Lệnh Telegram

| Lệnh | Mục đích |
| --- | --- |
| `/start`, `/help`, `/ping` | Khởi tạo chat, xem trợ giúp và kiểm tra bot |
| `/gki`, `/oki` | Tạo build qua hội thoại; quyền và hạn mức được kiểm tra |
| `/dl` | Tra cứu link tải kernel |
| `/build` | Build nhiều phiên bản theo batch, dành cho admin |
| `/st`, `/list` | Theo dõi job và lịch sử build |
| `/jobs`, `/canceljob ID` | Xem hàng đợi, hủy job thuộc quyền quản lý |
| `/resolvejob ID RUN_ID\|refund` | Admin xử lý yêu cầu GitHub chưa rõ kết quả sau khi kiểm tra |
| `/rb`, `/rebuild` | Chức năng rebuild dành cho admin |
| `/key`, `/keyvip`, `/keys` | Quản lý key dành cho owner |
| `/bc`, `/broadcast` | Gửi thông báo theo quyền quản trị |
| `/data` | Khôi phục dữ liệu theo quyền quản trị |

Các thao tác hủy job/batch hiển thị trong giao diện quản lý của bot. Dùng `/help` để xem cú pháp và quyền cụ thể theo phiên bản hiện tại.

## Dữ liệu và MongoDB

`STORAGE_PATH=data.json` mặc định lưu state tại thư mục gốc repository, không phụ thuộc thư mục làm việc khi khởi chạy. Local JSON là trạng thái hoạt động; `web/data/` là dữ liệu đã chuyển đổi cho dashboard, không thay thế backup state.

MongoDB là tùy chọn. Để chạy local-only, để trống `MONGODB_URI` và đặt `MONGODB_SYNC_MODE=off`. Khi dùng MongoDB, chọn chế độ và hostname writer theo `.env.example`; chỉ dùng một instance ghi cho cùng bộ dữ liệu. `pull` tải snapshot cloud lúc khởi tạo, thay state local nếu cloud có dữ liệu; đây không phải cơ chế đồng bộ realtime hai chiều. Chi tiết ở [kiến trúc](docs/architecture.md).

Bot dùng PyMongo Async thay Motor. Cloud snapshot được gộp và đẩy nền mỗi 30 giây; local vẫn được ghi ngay trước khi gửi build. Backup ZIP tự tạo lúc worker khởi động, sau đó mỗi 24 giờ, giữ 14 bản tại `data/backups/`. Backup chứa key/dữ liệu riêng tư: không đặt trong thư mục phục vụ web; sao chép bản backup ra ngoài VPS để phòng mất máy. Các biến mới đều có mặc định, không cần ghi đè `.env` đang chạy tốt. Xem [hướng dẫn nâng cấp/vận hành](docs/reliability.md).

## Kiểm tra source

```bash
python -m compileall -q bot tools scripts
# Nếu còn thư mục kiểm thử local:
python -m unittest discover -s tests -t .
node tests/test_dashboard_ui.cjs
cd web
npm ci
npm run build
```

Định dạng và kiểm tra Python thống nhất qua `pyproject.toml`:

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check bot tools scripts
python -m ruff format --check bot tools scripts
```

Kiểm tra tự động không thay thế một lượt build thử với repository/token của bạn. Có thể kiểm tra HTTP server bằng `curl http://127.0.0.1:5000/api/data` sau khi bot chạy.

`.gitignore` loại tests local, cache, dữ liệu runtime, backup, secrets và bundle `web/dist/`. Các bundle vẫn còn trên ổ đĩa; `start.sh` tự build lại khi triển khai. Giữ `web/package-lock.json`, `.env.example` và cấu hình build trong Git vì đây là đầu vào để dựng source.
