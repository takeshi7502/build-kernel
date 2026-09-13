#!/usr/bin/env bash
# Bootstrap and start the GKI services. Intended for Ubuntu/Debian VPS hosts.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

trap 'code=$?; echo ""; echo "❌ start.sh dừng ở dòng $LINENO (mã lỗi: $code)."; exit "$code"' ERR

MODE=""
CONFIGURE_SSL=0

usage() {
    cat <<'EOF'
Cách dùng:
  bash start.sh [bot|userbot|all] [--ssl]

Không truyền mode sẽ hiện menu. --ssl chỉ dùng khi muốn cài/cập nhật
Nginx + Let's Encrypt; các lần restart bình thường không cần dùng nó.

Ví dụ:
  bash start.sh bot
  bash start.sh all
  bash start.sh bot --ssl
EOF
}

for arg in "$@"; do
    case "$arg" in
        bot|userbot|all)
            if [ -n "$MODE" ]; then
                echo "❌ Chỉ được chọn một chế độ chạy."
                exit 2
            fi
            MODE="$arg"
            ;;
        --ssl) CONFIGURE_SSL=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "❌ Tham số không hợp lệ: $arg"; usage; exit 2 ;;
    esac
done

echo ""
echo "================================================="
echo "       GKI BOT - THIẾT LẬP VÀ KHỞI ĐỘNG          "
echo "================================================="

if [ ! -f ".env" ]; then
    echo "❌ Không tìm thấy .env. Hãy chạy: cp .env.example .env"
    exit 1
fi

choose_mode() {
    if [ -n "$MODE" ]; then
        return
    fi

    echo "Chọn chế độ chạy:"
    echo "  [1] Bot Telegram + Dashboard"
    echo "  [2] Chỉ Userbot"
    echo "  [3] Cả Bot và Userbot"
    read -r -p "Lựa chọn [1/2/3, mặc định 1]: " choice
    case "${choice:-1}" in
        1) MODE="bot" ;;
        2) MODE="userbot" ;;
        3) MODE="all" ;;
        *) echo "❌ Lựa chọn không hợp lệ. Chưa thay đổi process PM2 nào."; exit 2 ;;
    esac
}

ensure_command() {
    local command_name="$1"
    local package_name="$2"
    if command -v "$command_name" >/dev/null 2>&1; then
        return
    fi
    echo "⚙️  Thiếu $command_name; đang cài gói $package_name..."
    sudo apt-get update
    sudo apt-get install -y "$package_name"
}

ensure_pm2() {
    if command -v pm2 >/dev/null 2>&1; then
        return
    fi
    ensure_command npm npm
    echo "⚙️  Đang cài PM2..."
    sudo npm install -g pm2
}

ensure_venv_module() {
    # On Ubuntu, `import venv` can succeed even when ensurepip is absent.
    # Test actual venv creation in an isolated temporary directory instead.
    local probe_dir
    probe_dir="$(mktemp -d "$SCRIPT_DIR/.venv-check.XXXXXX")"
    if python3 -m venv "$probe_dir" >/dev/null 2>&1; then
        rm -rf -- "$probe_dir"
        return
    fi
    rm -rf -- "$probe_dir"
    echo "⚙️  Đang cài python3-venv (bổ sung ensurepip)..."
    sudo apt-get update
    sudo apt-get install -y python3-venv
}

ensure_runtime() {
    ensure_command python3 python3
    ensure_pm2

    if [ -d "venv" ] && { [ ! -x "venv/bin/python" ] || [ ! -x "venv/bin/pip" ]; }; then
        local backup_name
        backup_name="venv.incomplete.$(date +%Y%m%d%H%M%S)"
        echo "⚠️  Phát hiện venv tạo dở; lưu lại thành $backup_name"
        mv venv "$backup_name"
    fi

    if [ ! -x "venv/bin/python" ] || [ ! -x "venv/bin/pip" ]; then
        echo "⚙️  Đang tạo Python virtual environment..."
        ensure_venv_module
        python3 -m venv venv
    fi

    echo "📦 Đồng bộ Python dependencies..."
    ./venv/bin/pip install --disable-pip-version-check -q -r requirements.txt
}

ensure_dashboard_assets() {
    if [ -f "web/dist/app.bundle.js" ] && [ -f "web/dist/style.bundle.css" ]; then
        return
    fi

    echo "⚠️  Thiếu web/dist. Cần build frontend trước khi mở dashboard."
    if ! command -v npm >/dev/null 2>&1; then
        echo "❌ Không có npm để build frontend."
        exit 1
    fi
    (
        cd web
        if [ ! -d node_modules ]; then
            npm ci
        fi
        npm run build
    )
}

ensure_data_layout() {
    local data_dir data_file
    echo "📁 Kiểm tra thư mục dữ liệu dashboard..."
    for data_dir in android12 android13 android14 android15 android16; do
        mkdir -p "web/data/$data_dir"
    done

    for data_file in \
        android12/5.10.json \
        android13/5.15.json \
        android14/6.1.json \
        android15/6.6.json \
        android16/6.12.json; do
        if [ ! -f "web/data/$data_file" ]; then
            printf '%s\n' '{"entries":[]}' > "web/data/$data_file"
            echo "   >> Đã tạo web/data/$data_file"
        fi
    done

    if [ ! -f "web/data/announcement.json" ]; then
        printf '%s\n' '{"items":[]}' > web/data/announcement.json
        echo "   >> Đã tạo web/data/announcement.json"
    fi
}

env_value() {
    # Read only the exact KEY= assignment; do not source .env as shell code.
    local key="$1"
    awk -F '=' -v wanted="$key" '
        $0 !~ /^[[:space:]]*#/ && $1 ~ "^[[:space:]]*" wanted "[[:space:]]*$" {
            value = substr($0, index($0, "=") + 1)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
            print value
            exit
        }
    ' .env
}

web_port() {
    local port
    port="$(env_value WEB_PORT)"
    port="${port:-5000}"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
        echo "❌ WEB_PORT phải là số từ 1 đến 65535 (hiện là: $port)." >&2
        exit 1
    fi
    printf '%s\n' "$port"
}

configure_ssl() {
    local domain email port
    port="$(web_port)"

    echo ""
    echo "🔐 Cấu hình Nginx + Let's Encrypt"
    read -r -p "Tên miền dashboard (vd: kernel.example.com): " domain
    domain="${domain,,}"
    if ! [[ "$domain" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]; then
        echo "❌ Tên miền không hợp lệ."
        exit 1
    fi

    email="${CERTBOT_EMAIL:-}"
    if [ -z "$email" ]; then
        read -r -p "Email nhận thông báo gia hạn chứng chỉ: " email
    fi
    if ! [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
        echo "❌ Cần một email hợp lệ cho Let's Encrypt."
        exit 1
    fi

    sudo apt-get update
    sudo apt-get install -y nginx certbot python3-certbot-nginx

    sudo tee /etc/nginx/sites-available/gki-bot >/dev/null <<EOF
server {
    listen 80;
    server_name $domain;

    location / {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

    sudo ln -sfn /etc/nginx/sites-available/gki-bot /etc/nginx/sites-enabled/gki-bot
    sudo nginx -t
    sudo systemctl reload nginx

    # Do not expose WEB_PORT through UFW: Nginx proxies to localhost.
    if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q "Status: active"; then
        sudo ufw allow 'Nginx Full'
    fi

    sudo certbot --nginx -d "$domain" --redirect --non-interactive --agree-tos --email "$email"
    echo "✅ HTTPS đã sẵn sàng: https://$domain"
}

process_exists() {
    pm2 describe "$1" >/dev/null 2>&1
}

start_process() {
    local name="$1"
    local file="$2"
    if process_exists "$name"; then
        echo "🔄 Restart $name..."
        pm2 restart "$name" --update-env
    else
        echo "🚀 Start $name..."
        pm2 start "$file" --name "$name" --interpreter "$SCRIPT_DIR/venv/bin/python" --cwd "$SCRIPT_DIR" --time
    fi
}

stop_process_if_present() {
    if process_exists "$1"; then
        echo "⏹️  Stop $1 (không xoá cấu hình PM2)..."
        pm2 stop "$1"
    fi
}

choose_mode
ensure_runtime
ensure_data_layout

if [ "$MODE" = "bot" ] || [ "$MODE" = "all" ]; then
    ensure_dashboard_assets
fi

case "$MODE" in
    bot)
        stop_process_if_present gki-userbot
        start_process gki-bot bot/main.py
        ;;
    userbot)
        stop_process_if_present gki-bot
        start_process gki-userbot bot/userbot.py
        ;;
    all)
        start_process gki-bot bot/main.py
        start_process gki-userbot bot/userbot.py
        ;;
esac

pm2 save

# SSL is optional infrastructure. Start the requested bot services first so a
# DNS/ACME failure cannot leave the bot offline.
if [ "$CONFIGURE_SSL" -eq 1 ]; then
    configure_ssl
fi

echo ""
echo "✅ Hoàn tất."
pm2 status gki-bot gki-userbot || true
if [ "$MODE" = "bot" ] || [ "$MODE" = "all" ]; then
    port="$(web_port)"
    echo "🌐 Dashboard nội bộ: http://127.0.0.1:$port"
    if [ "$CONFIGURE_SSL" -eq 0 ]; then
        echo "   Cần cài HTTPS? Chạy: bash start.sh $MODE --ssl"
    fi
fi
