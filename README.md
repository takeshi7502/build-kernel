# 🔧 GKI Kernel Build System

Hệ thống tự động hóa quá trình compile GKI & Custom Kernel thông qua GitHub Actions, quản lý bằng Telegram Bot và theo dõi qua Web Dashboard Realtime.

## ✨ Tính năng nổi bật

- 🤖 **Telegram Bot Management** — Tự động deploy Kernel Build flow thông qua hội thoại (`/gki`) hoặc lệnh (`/build`). 
- 🌐 **Web Dashboard Realtime** — Giao diện hiển thị trực tiếp tiến trình build của toàn bộ server, trạng thái queue, và logs của từng phiên bản.
- 📦 **Batch Queue System** — Lên lịch build cùng lúc hàng loạt version (ví dụ: 20 bản 5.10.x), hệ thống tự xếp hàng và quản lý slot GitHub.
- 🗄️ **Hybrid Storage** — Lưu trữ mượt mà kết hợp tính linh hoạt của Local JSON + MongoDB cho độ nạp tối ưu, lưu trữ lâu dài.
- 📰 **Telegraph & Downloads** — Tự động get artifacts và tạo link tải trực tiếp vô cùng sạch sẽ thông qua bài viết Telegraph.
- 🗑️ **Cancel & Cleanup** — Dừng một chuỗi build đang chạy (`/cancelbatch`) hoặc huỷ đơn lẻ các tiến trình lỗi, bot sẽ tự dọn rác cả trên GitHub Actions để tiết kiệm bộ nhớ.
- 🔐 **Multi-role Permission** — Quản trị viên/User whitelist, cấp key giới hạn lượt build cho mem.

## 📂 Kiến trúc hệ thống

```text
├── bot/                 # Source code Bot Telegram (Python)
│   ├── main.py          # Entry point chính của Bot & Poller
│   ├── web_sync.py      # Core đồng bộ trạng thái Realtime cho Web Dashboard
│   ├── buildsave.py     # Flow dành riêng cho chức năng Batch Build (hàng đợi)
│   ├── gki.py           # Flow tạo phân nhánh build đơn cơ bản
│   └── userbot.py       # Module telethon dự phòng
├── web/                 # Source code Web Dashboard (Node.js/React/Vite)
│   ├── index.html       
│   ├── js/              # Mã nguồn xử lý render giao diện thẻ Masonry theo từng Batch
│   ├── src/             # Component mở rộng
│   └── data/            # Thư mục giao tiếp CSDL JSON JSON giữa Bot và Frontend
├── ecosystem.config.js  # Cấu hình PM2 để Start đồng loạt nhiều dịch vụ
└── requirements.txt     # Python Dependencies
```

## 📝 Danh sách lệnh chính trên Telegram

| Lệnh | Role | Mô tả |
|------|------|-------|
| `/gki` | Admin / User | Bắt đầu tạo 1 bản build riêng lẻ bằng giao diện bấm nút |
| `/build` | Admin | Tạo Batch Queue (build hàng loạt nhiều version cùng lúc vào lưu trữ) |
| `/st` | Admin | Kiểm tra tiến trình tất cả các job đang chờ/đang build |
| `/list` | Admin | Lịch sử build thành công & Lấy link tải Telegraph |
| `/cancel _id` | Admin | Huỷ 1 tiến trình đang chạy đơn lẻ |
| `/cancelbatch_id` | Admin | Huỷ toàn bộ một đợt batch queuing đang chạy để clear slot |
| `/key` | Owner | Quản lý whitelist member (Tạo thêm lượt build cho mem) |

---

## 🚀 Hướng dẫn Triển khai Server (VPS Ubuntu/Debian)

Hệ thống được thiết kế tối ưu nhất khi chạy với **PM2** và có **MongoDB**.

### Bước 1: Cài đặt phần mềm cơ sở
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv git curl -y

# Cài Node.js & PM2
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g pm2
```

### Bước 2: Clone Repo
```bash
git clone https://github.com/takeshi7502/build-kernel.git
cd build-kernel
```

### Bước 3: Build Web Dashboard (Frontend)
```bash
cd web
npm install
npm run build
cd ..
```

### Bước 4: Môi trường ảo Bot (Backend Python)
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Bước 5: Cấu hình Môi trường `.env`
Thiết lập file cấu hình bí mật:
```bash
cp .env.example .env
nano .env
```
Nội dung file `.env` cần chuẩn bị:
```env
TELEGRAM_BOT_TOKEN=7xxx:AAHxxx...
GITHUB_TOKEN=ghp_...
GITHUB_OWNER=Quản_trị_viên_repo_Github
GKI_REPO=Tên_Repo_Action_Của_Bạn
MONGO_URI=mongodb://localhost:27017/gki_build
OWNER_ID=ID_Telegram_Của_Bạn
ADMIN_IDS=Các_Admin_Khác
```

### Bước 6: Khởi chạy toàn bộ hệ thống
Sử dụng PM2 ecosystem để spawn toàn bộ các process liên quan:
```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

Kiểm tra trạng thái hệ thống:
```bash
pm2 status
```

---

## 🎨 Tích hợp Userbot (Telethon)
Nếu Bot API bị giới hạn tín hiệu vì lý do nào đó, bạn có thể triển khai thêm `userbot.py` để gửi tín hiệu bypass API bot. Chỉnh sửa `.env`:
```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION=gki_user
```
Khởi chạy `pm2 start bot/userbot.py --name gki-userbot`.

## 📄 Bản quyền
MIT License. Cảm ơn sự hỗ trợ từ các Open Source Repository và The Android Open Source Project. Mọi thắc mắc kỹ thuật có thể mở Issue.
