#!/bin/bash

echo ""
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

# ─── BƯỚC 5: Cài đặt SSL (Nginx + Certbot) ───────────────────────────────────
echo ""
echo "🔐 Thiết lập SSL cho Web Dashboard..."
echo ""
read -p "   Bạn có muốn cài SSL (HTTPS) cho Web Dashboard không? [y/N]: " ssl_choice
ssl_choice=${ssl_choice:-n}

if [[ "$ssl_choice" =~ ^[Yy]$ ]]; then
    read -p "   Nhập tên miền của bạn (VD: kernel.takeshi.dev): " DOMAIN
    DOMAIN=$(echo "$DOMAIN" | tr -d ' ')

    if [ -z "$DOMAIN" ]; then
        echo "   ⚠️  Bỏ qua cài SSL (không có tên miền)."
    else
        WEB_PORT=$(grep "WEB_PORT" .env | cut -d '=' -f2)
        WEB_PORT=${WEB_PORT:-5000}

        echo ""
        echo "   ⚙️  Đang cài Nginx & Certbot..."
        sudo apt update -qq
        sudo apt install -y nginx certbot python3-certbot-nginx > /dev/null 2>&1
        echo "   ✅ Cài xong!"

        # Mở firewall — bắt buộc để Certbot xác minh và Nginx hoạt động
        echo "   🔓 Đang mở Firewall (UFW) cho các port cần thiết..."
        sudo ufw allow 22/tcp    > /dev/null 2>&1   # SSH - tránh bị khóa ra ngoài
        sudo ufw allow 80/tcp    > /dev/null 2>&1   # HTTP - Certbot cần để xác minh
        sudo ufw allow 443/tcp   > /dev/null 2>&1   # HTTPS
        sudo ufw allow "$WEB_PORT/tcp" > /dev/null 2>&1  # Web dashboard port
        sudo ufw --force enable  > /dev/null 2>&1
        echo "   ✅ Đã mở port 22, 80, 443, $WEB_PORT!"


        # Tạo config Nginx cho domain
        echo "   ⚙️  Đang cấu hình Nginx reverse proxy cho $DOMAIN..."
        sudo tee /etc/nginx/sites-available/gki-bot > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$WEB_PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
    }
}
EOF

        # Kích hoạt site
        sudo ln -sf /etc/nginx/sites-available/gki-bot /etc/nginx/sites-enabled/gki-bot
        sudo rm -f /etc/nginx/sites-enabled/default 2>/dev/null
        sudo nginx -t && sudo systemctl reload nginx
        echo "   ✅ Nginx đã chạy!"

        # Lấy chứng chỉ SSL từ Let's Encrypt
        echo ""
        echo "   🔐 Đang xin chứng chỉ SSL từ Let's Encrypt cho $DOMAIN..."
        echo "   (Yêu cầu: DNS của $DOMAIN phải đang trỏ đúng về IP VPS này!)"
        echo ""
        sudo certbot --nginx -d "$DOMAIN" --redirect --agree-tos --register-unsafely-without-email --non-interactive

        if [ $? -eq 0 ]; then
            echo ""
            echo "   ✅ SSL đã được cài thành công!"
            echo "   🌐 Web Dashboard: https://$DOMAIN"
            echo "   🔄 Chứng chỉ sẽ tự gia hạn (auto-renew) qua cron."
            # Thêm auto-renew vào cron nếu chưa có
            (crontab -l 2>/dev/null | grep -q "certbot renew") || \
                (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet && systemctl reload nginx") | crontab -
        else
            echo ""
            echo "   ⚠️  Certbot thất bại! Có thể DNS chưa trỏ đúng."
            echo "   👉 Thử lại sau bằng lệnh: sudo certbot --nginx -d $DOMAIN --redirect"
        fi
    fi
else
    echo "   ⏭️  Bỏ qua cài SSL."
fi

# ─── BƯỚC 6: Chọn chế độ chạy ────────────────────────────────────────────────
echo ""
echo "Vui lòng chọn chế độ chạy Bot:"
echo "  [1] Chỉ chạy Bot Telegram (main.py)           — Tiêu chuẩn"
echo "  [2] Chỉ chạy Userbot (userbot.py)             — Tính năng cá nhân"
echo "  [3] Chạy CẢ HAI (Bot + Userbot)               — Đầy đủ tính năng ✨"
read -p "Nhập lựa chọn của bạn (1/2/3) [Mặc định là 1]: " choice

choice=${choice:-1}

echo ""
echo "🚀 Đang khởi động tiến trình theo lựa chọn [$choice]..."

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

pm2 save > /dev/null 2>&1

# ─── BƯỚC 7: Tổng kết ─────────────────────────────────────────────────────────
echo ""
echo "================================================="
echo "✅ HOÀN TẤT KHỞI ĐỘNG!"
echo ""
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
echo "🔄 CẬP NHẬT CODE:"
echo "   bash run.sh   (chọn option 1)"
echo ""
echo "💾 BACKUP & KHÔI PHỤC DATA:"
echo "   .backup (trong Telegram) → gửi file zip về thiết bị bác"
echo "   Reply file zip + /data   → khôi phục lên VPS mới"
echo "================================================="

WEB_PORT=$(grep "WEB_PORT" .env | cut -d '=' -f2)
WEB_PORT=${WEB_PORT:-5000}
if command -v curl &> /dev/null; then
    VPS_IP=$(curl -4 -s --max-time 3 ifconfig.me 2>/dev/null)
    if [ -n "$VPS_IP" ]; then
        echo ""
        echo "🌐 TRUY CẬP WEB DASHBOARD:"
        echo "   HTTP  : http://${VPS_IP}:${WEB_PORT}"
        if [ -n "$DOMAIN" ]; then
            echo "   HTTPS : https://${DOMAIN}"
        fi
        echo "================================================="
    fi
fi
