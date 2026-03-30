#!/bin/bash

echo "================================================="
echo "       GKI BOT - STARTUP SCRIPT VỚI PM2          "
echo "================================================="

# ─── BƯỚC 1: Kiểm tra file .env ───────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "❌ LỖI NGHIÊM TRỌNG: Không tìm thấy file '.env'!"
    echo "   Bạn chưa cấu hình token cho Bot."
    echo "   👉 Cách sửa: Hãy copy file '.env.example' thành '.env'"
    echo "   Lệnh gõ nhanh: cp .env.example .env"
    echo "   Sau đó mở file .env lên và điền đầy đủ các token rồi mới chạy lại file này nhé!"
    exit 1
fi

# ─── BƯỚC 2: Tạo cấu trúc thư mục Data nếu chưa có ───────────────────────────
echo "📁 Kiểm tra cấu trúc thư mục dữ liệu..."
for dir in web/data/android12 web/data/android13 web/data/android14 web/data/android15 web/data/android16; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        echo "   >> Đã tạo thư mục: $dir"
    fi
done

# Tạo file JSON rỗng nếu chưa tồn tại (tránh crash khi bot đọc lần đầu)
for f in web/data/android12/5.10.json web/data/android13/5.15.json web/data/android14/6.1.json web/data/android15/6.6.json web/data/android16/6.12.json web/data/announcement.json; do
    if [ ! -f "$f" ]; then
        echo '{"entries":[]}' > "$f"
        echo "   >> Đã tạo file mẫu: $f"
    fi
done

# ─── BƯỚC 3: Cài đặt PM2 nếu chưa có ────────────────────────────────────────
if ! command -v pm2 &> /dev/null; then
    echo "⚙️  Phát hiện VPS chưa cài đặt PM2. Đang tự động cài PM2..."
    sudo apt update && sudo apt install -y nodejs npm
    sudo npm install -g pm2
    echo "✅ Đã cài đặt xong PM2!"
fi

# ─── BƯỚC 4: Thiết lập Python Virtual Environment ────────────────────────────
echo "📦 Đang thiết lập môi trường Python..."
if [ ! -d "venv" ]; then
    echo "   >> Đang tạo thư mục venv..."
    sudo apt install -y python3-venv > /dev/null 2>&1
    python3 -m venv venv
fi

echo "   >> Đang nạp các thư viện từ requirements.txt..."
./venv/bin/pip install -q -r requirements.txt
echo "   ✅ Thư viện đã sẵn sàng!"

# ─── BƯỚC 5: Chọn chế độ chạy ────────────────────────────────────────────────
echo ""
echo "Vui lòng chọn chế độ chạy Bot:"
echo "  [1] Chỉ chạy Bot Telegram (main.py)           — Tiêu chuẩn"
echo "  [2] Chỉ chạy Userbot (userbot.py)             — Tính năng cá nhân"
echo "  [3] Chạy CẢ HAI (Bot + Userbot)               — Đầy đủ tính năng ✨"
read -p "Nhập lựa chọn của bạn (1/2/3) [Mặc định là 1]: " choice

choice=${choice:-1}

echo ""
echo "🚀 Đang khởi động tiến trình theo lựa chọn [$choice]..."

# Dọn dẹp tiến trình cũ (nếu có)
pm2 stop gki-bot gki-userbot > /dev/null 2>&1
pm2 delete gki-bot gki-userbot > /dev/null 2>&1

INTERPRETER="./venv/bin/python"

if [ "$choice" == "1" ]; then
    pm2 start bot/main.py --interpreter "$INTERPRETER" --name "gki-bot"
elif [ "$choice" == "2" ]; then
    pm2 start bot/userbot.py --interpreter "$INTERPRETER" --name "gki-userbot"
elif [ "$choice" == "3" ]; then
    pm2 start bot/main.py --interpreter "$INTERPRETER" --name "gki-bot"
    pm2 start bot/userbot.py --interpreter "$INTERPRETER" --name "gki-userbot"
else
    echo "❌ Lựa chọn không hợp lệ. Đang thoát..."
    exit 1
fi

# Lưu cấu hình PM2 và thiết lập khởi động cùng hệ thống
pm2 save > /dev/null 2>&1

# ─── BƯỚC 6: Hiển thị kết quả ─────────────────────────────────────────────────
echo ""
echo "================================================="
echo "✅ HOÀN TẤT KHỞI ĐỘNG!"
echo "Bảng trạng thái các Bot đang chạy ngầm trên VPS:"
pm2 status

echo ""
echo "📌 CÁC LỆNH PM2 CẦN BIẾT:"
echo "   pm2 list                  — Xem bảng điều khiển"
echo "   pm2 log gki-bot           — Xem log thời gian thực"
echo "   pm2 restart gki-bot       — Khởi động lại bot"
echo "   pm2 restart all           — Khởi động lại tất cả"
echo "   pm2 flush                 — Xoá trắng log cũ"
echo "   pm2 startup               — Tự bật bot khi VPS reboot"
echo ""
echo "🔄 CẬP NHẬT CODE MỚI NHẤT:"
echo "   git fetch --all && git reset --hard origin/main && pm2 restart all"
echo ""
echo "💾 BACKUP & KHÔI PHỤC DATA:"
echo "   Gõ .backup (trong Telegram) để tải file zip backup về máy"
echo "   Reply file zip đó rồi gõ /data để khôi phục data lên VPS mới"
echo "================================================="

WEB_PORT=$(grep "WEB_PORT" .env | cut -d '=' -f2)
WEB_PORT=${WEB_PORT:-5000}
if command -v curl &> /dev/null; then
    VPS_IP=$(curl -4 -s --max-time 3 ifconfig.me 2>/dev/null)
    if [ -n "$VPS_IP" ]; then
        echo ""
        echo "================================================="
        echo "🌐 BẢNG ĐIỀU KHIỂN WEB (REAL-TIME)"
        echo "Truy cập ngay vào: http://${VPS_IP}:${WEB_PORT}"
        echo "================================================="
    fi
fi
