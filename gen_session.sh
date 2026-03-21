#!/bin/bash
clear
echo "🚀 Đang khởi chạy Công Cụ tạo Telegram String Session..."

# Cài đặt telethon và python-dotenv nếu chưa có
echo "Đang kiểm tra thư viện..."
pip3 install telethon python-dotenv -q >/dev/null 2>&1 || pip install telethon python-dotenv -q >/dev/null 2>&1

# Chạy theo python3 hoặc python tuỳ hệ thống Linux
if command -v python3 &>/dev/null; then
    python3 generate_session.py
else
    python generate_session.py
fi
