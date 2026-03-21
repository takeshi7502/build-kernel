#!/bin/bash

echo "================================================="
echo "       GKI BOT - STARTUP SCRIPT VỚI PM2          "
echo "================================================="

# 1. Kiểm tra xem file .env có tồn tại không
if [ ! -f ".env" ]; then
    echo "❌ LỖI NGHIÊM TRỌNG: Không tìm thấy file '.env'!"
    echo "   Bạn chưa cấu hình token cho Bot."
    echo "   👉 Cách sửa: Hãy copy file '.env.example' thành '.env'"
    echo "   Lệnh gõ nhanh: cp .env.example .env"
    echo "   Sau đó mở file .env lên và điền đầy đủ các token rồi mới chạy lại file này nhé!"
    exit 1
fi

# 2. Cài đặt pm2 nếu chưa có
if ! command -v pm2 &> /dev/null; then
    echo "⚙️  Phát hiện VPS chưa cài đặt PM2. Đang tự động cài PM2..."
    sudo apt update && sudo apt install -y nodejs npm
    sudo npm install -g pm2
    echo "✅ Đã cài đặt xong PM2!"
fi

# 3. Cài đặt thư viện Python qua Virtual Environment (venv)
echo "📦 Đang thiết lập môi trường Python..."
if [ ! -d "venv" ]; then
    echo "   >> Đang tạo thư mục venv..."
    sudo apt install -y python3-venv > /dev/null 2>&1
    python3 -m venv venv
fi

echo "   >> Đang nạp các thư viện từ requirements.txt..."
./venv/bin/pip install -r requirements.txt

# 3. Cho người dùng chọn chế độ
echo ""
echo "Vui lòng chọn chế độ chạy Bot:"
echo "  [1] Chỉ chạy Bot Telegram (main.py) - MẶC ĐỊNH"
echo "  [2] Chỉ chạy Userbot (userbot.py)"
echo "  [3] Chạy CẢ HAI (Bot và Userbot)"
read -p "Nhập lựa chọn của bạn (1/2/3) [Mặc định là 1]: " choice

# Nếu người dùng bấm Enter (bỏ trống), gán choice = 1
choice=${choice:-1}

echo ""
echo "🚀 Đang khởi động tiến trình theo lựa chọn [$choice]..."

# Xoá các tiến trình cũ (nếu có) để tránh chạy đè/trùng lặp
pm2 stop gki-bot gki-userbot > /dev/null 2>&1
pm2 delete gki-bot gki-userbot > /dev/null 2>&1

# 5. Chạy bot bằng pm2 với interpreter là Python trong venv
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

# Lưu cấu hình PM2
pm2 save > /dev/null 2>&1

# 5. Hiển thị kết quả và lệnh quản lý
echo ""
echo "================================================="
echo "✅ HOÀN TẤT KHỞI ĐỘNG!"
echo "Bảng trạng thái các Bot đang chạy ngầm trên VPS:"
pm2 status

echo ""
echo "📌 CÁC LỆNH QUẢN LÝ PM2 BẠN CẦN BIẾT:"
echo " - Xem log (tin nhắn/lỗi):   pm2 logs"
echo " - Xem log riêng 1 bot:      pm2 logs gki-bot"
echo " - Dừng một bot:             pm2 stop gki-bot"
echo " - Khởi động lại bot:        pm2 restart gki-bot"
echo " - Tắt hẳn và xoá bot:       pm2 delete gki-bot"
echo " - Theo dõi tài nguyên:      pm2 monit"
echo ""
echo "🔹 (Mẹo: Nếu muốn VPS tự bật bot khi bị khởi động lại (Reboot),"
echo "    hãy gõ lệnh: pm2 startup)"
echo "================================================="
