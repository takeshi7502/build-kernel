#!/bin/bash
echo "========================================="
echo "⚙️  KIỂM TRA VÀ CÀI ĐẶT MÔI TRƯỜNG PYTHON"
echo "========================================="

# 1. Kiểm tra python3
if ! command -v python3 &> /dev/null; then
    echo "📦 Máy chủ chưa có Python3, đang tiến hành cài đặt..."
    sudo apt update -y
    sudo apt install python3 -y
fi

# 2. Kiểm tra pip3
if ! command -v pip3 &> /dev/null; then
    echo "📦 Máy chủ chưa có pip3, đang tiến hành cài đặt..."
    sudo apt update -y
    sudo apt install python3-pip -y
fi

# 3. Cài đặt các thư viện cần thiết (có xử lý lỗi PEP 668 PEP-668 environments)
echo "📦 Đang cài đặt thư viện Telethon và Cryptg..."
# Thử cài đặt bình thường
if ! pip3 install telethon cryptg -q; then
    # Kể từ Ubuntu 23/24, pip không cho cài trực tiếp ra root, phải gọi flag break-system-packages
    echo "⚠️ Bắt buộc phải vượt qua cảnh báo môi trường của Ubuntu..."
    pip3 install telethon cryptg --break-system-packages -q
fi

echo "✅ Cài đặt môi trường hoàn tất!"
echo ""

# Chạy trình tạo Session String, dùng đúng cấu trúc thư mục mới
if [ -f "bot/gen_session.py" ]; then
    python3 bot/gen_session.py
elif [ -f "gen_session.py" ]; then
    # Đề phòng user cd thẳng vào thư mục bot
    python3 gen_session.py
else
    echo "❌ Không tìm thấy script trình tạo Session (bot/gen_session.py). Vui lòng tải đầy đủ mã nguồn!"
fi
