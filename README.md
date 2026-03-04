# 🔧 GKI Kernel Build Bot

Telegram bot để tự động build GKI Kernel thông qua GitHub Actions.

## ✨ Tính năng

- 🚀 **Build GKI Kernel** — Chọn variant, nhánh, phiên bản Android và dispatch workflow trực tiếp từ Telegram
- 📋 **Lịch sử build** (`/list`) — Xem danh sách build thành công với phân trang, link tải qua Telegraph
- 📊 **Trạng thái** (`/status`) — Theo dõi build đang chạy, ước tính thời gian còn lại, hủy build
- 🔐 **Phân quyền** — Owner / Admin / User với key system
- 🔔 **Thông báo tự động** — Bot gửi kết quả khi build xong, thông báo khi hệ thống rảnh
- 📰 **Telegraph** — Tự động tạo trang Telegraph chứa danh sách file tải về (gọn gàng)

## 📁 Cấu trúc

```
├── main.py            # Bot chính, storage, poller, commands
├── gki.py             # GKI build conversation flow
├── config.py          # Load & validate config từ .env
├── permissions.py     # is_owner() / is_admin() helpers
├── requirements.txt   # Dependencies
├── .env.example       # Template config (push lên GitHub)
├── .env               # Config thật (KHÔNG push)
├── data.json          # Dữ liệu runtime (KHÔNG push)
└── .gitignore
```

## 🔑 Bảng phân quyền

| Role | Quyền | Cách thiết lập |
|------|-------|----------------|
| **Owner** | Toàn quyền: `/key`, `/status`, `/list`, bypass key & job limit | `OWNER_ID` trong `.env` |
| **Admin** | `/status`, `/list`, build không cần key, bypass job limit | `ADMIN_IDS` trong `.env` |
| **User** | Build bằng `/gki {key}`, giới hạn 1 job / 3 giờ | Ai cũng được |

## 📝 Danh sách lệnh

| Lệnh | Quyền | Mô tả |
|------|-------|-------|
| `/gki` hoặc `/gki {key}` | Admin / User | Bắt đầu build GKI Kernel |
| `/status` | Admin | Xem build đang chạy + nút hủy |
| `/list` | Admin | Lịch sử build thành công (phân trang) |
| `/key {mã} {số_lượt}` | Owner | Tạo/cập nhật key cho user |

---

## 🖥️ Chạy trên máy (Local)

### Yêu cầu
- Python 3.10+
- Git
- Telegram Bot Token (từ [@BotFather](https://t.me/BotFather))
- GitHub Fine-grained PAT (quyền **Actions: Read and Write**)

### Bước 1: Clone repo

```bash
git clone https://github.com/<username>/<repo>.git
cd <repo>
```

### Bước 2: Tạo môi trường ảo (khuyến khích)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate
```

### Bước 3: Cài dependencies

```bash
pip install -r requirements.txt
```

### Bước 4: Cấu hình

```bash
# Copy file mẫu
cp .env.example .env

# Mở .env và điền thông tin thật
```

Nội dung `.env` cần điền:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
GITHUB_OWNER=your_github_username
GKI_REPO=your_gki_repo
GKI_DEFAULT_BRANCH=main
GKI_WORKFLOWS=Build=main.yml
OWNER_ID=123456789
ADMIN_IDS=
```

> 💡 **Lấy `OWNER_ID`**: Gửi tin nhắn cho [@userinfobot](https://t.me/userinfobot) trên Telegram để biết ID của bạn.

> 💡 **Tạo GitHub Token**: GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Chọn repo → Permission: **Actions: Read and Write**

### Bước 5: Chạy bot

```bash
python main.py
```

---

## 🌐 Chạy trên VPS (Ubuntu)

### Bước 1: Cài đặt Python

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv git -y
```

### Bước 2: Clone repo

```bash
cd ~
git clone https://github.com/<username>/<repo>.git
cd <repo>
```

### Bước 3: Tạo môi trường ảo

```bash
python3 -m venv venv
source venv/bin/activate
```

### Bước 4: Cài dependencies

```bash
pip install -r requirements.txt
```

### Bước 5: Cấu hình

```bash
cp .env.example .env
nano .env
# Điền đầy đủ thông tin, Ctrl+O để lưu, Ctrl+X để thoát
```

### Bước 6: Chạy nền với `systemd` (khuyến khích)

Tạo service file:

```bash
sudo nano /etc/systemd/system/gki-bot.service
```

Dán nội dung sau (thay `<user>` và `<repo>` cho đúng):

```ini
[Unit]
Description=GKI Kernel Build Bot
After=network.target

[Service]
Type=simple
User=<user>
WorkingDirectory=/home/<user>/<repo>
ExecStart=/home/<user>/<repo>/venv/bin/python main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Kích hoạt và chạy:

```bash
sudo systemctl daemon-reload
sudo systemctl enable gki-bot
sudo systemctl start gki-bot
```

Xem log:

```bash
# Xem log realtime
sudo journalctl -u gki-bot -f

# Xem trạng thái
sudo systemctl status gki-bot
```

### Hoặc chạy nhanh với `screen`

```bash
screen -S gki-bot
source venv/bin/activate
python main.py
# Nhấn Ctrl+A rồi D để detach

# Quay lại xem:
screen -r gki-bot
```

---

## 📄 License

MIT
