#!/usr/bin/env bash
# Configure this bot's Nginx site; never alter other virtual hosts.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

usage() {
    echo "Usage: bash run.sh web [kernel.example.com]"
    echo "Nhập domain (hoặc URL https://...) và email tùy chọn; tự cấu hình Nginx/HTTPS."
}
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then usage; exit 0; fi
if [ "$#" -gt 1 ]; then usage >&2; exit 2; fi
if [ ! -x venv/bin/python ] || [ ! -f .env ] || ! command -v pm2 >/dev/null 2>&1; then
    echo "Cần cài bot trước: bash start.sh" >&2
    exit 1
fi
if pm2 describe gki-userbot >/dev/null 2>&1; then
    echo "Hãy chạy bash run.sh migrate trước." >&2
    exit 1
fi

as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}
helper() { venv/bin/python scripts/web_config.py "$@"; }

domain="${1:-}"
if [ -z "$domain" ]; then read -r -p "Domain dashboard (vd kernel.takeshi.dev): " domain; fi
domain="$(helper domain "$domain")"
port="$(helper port)"
if [ "$port" -eq 80 ] || [ "$port" -eq 443 ]; then
    echo "WEB_PORT phải khác 80/443 để nhường các cổng này cho Nginx." >&2
    exit 1
fi
email="$(helper email)"
if [ -t 0 ]; then
    read -r -p "Email gia hạn SSL [${email:-bỏ trống nếu không dùng}]: " entered_email
    email="${entered_email:-$email}"
fi
if [ -n "$email" ] && ! [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
    echo "Email không hợp lệ." >&2; exit 2
fi
addresses="$(helper dns "$domain")"
echo "DNS của $domain:"
printf '%s\n' "$addresses"
echo "Mọi bản ghi A/AAAA phải trỏ tới VPS này (hoặc proxy về VPS); cổng 80/443 phải truy cập được."

# One setup at a time; no secrets are placed in this lock file.
exec 9> .web-setup.lock
if ! flock -n 9; then echo "Một phiên cấu hình web khác đang chạy." >&2; exit 1; fi

site=/etc/nginx/sites-available/gki-bot
enabled=/etc/nginx/sites-enabled/gki-bot
acme_root=/var/www/gki-bot-acme
previous=""
had_enabled=0
env_changed=0
candidate="$(mktemp "$SCRIPT_DIR/.web-config-XXXXXX")"
probe="gki-setup-$(date +%s)-$$-$RANDOM"
challenge="$acme_root/.well-known/acme-challenge/$probe"

cleanup() {
    local result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        echo "Cấu hình chưa hoàn tất; chưa xác nhận web HTTPS hoạt động." >&2
        if [ "$env_changed" -eq 1 ]; then
            helper restore-env || true
            pm2 startOrRestart deploy/ecosystem.config.cjs --only gki-bot --update-env >/dev/null 2>&1 || true
        fi
        if [ -n "$previous" ]; then
            as_root cp -- "$previous" "$site" || true
            if [ "$had_enabled" -eq 0 ]; then as_root rm -f -- "$enabled" || true; fi
            if as_root nginx -t; then as_root systemctl reload nginx || true; fi
            echo "Đã khôi phục cấu hình Nginx cũ từ $previous." >&2
        fi
        echo "Kiểm tra DNS A/AAAA, firewall VPS/security group cổng 80/443, rồi chạy lại bash run.sh web." >&2
    fi
    rm -f -- "$candidate"
    if [ -f "$challenge" ]; then as_root rm -f -- "$challenge"; fi
    exit "$result"
}
trap cleanup EXIT

if ! command -v nginx >/dev/null 2>&1 || ! command -v certbot >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1 \
        || ! dpkg-query -W -f='${Status}' python3-certbot-nginx 2>/dev/null | grep -q 'install ok installed'; then
    as_root apt-get update
    as_root apt-get install -y nginx certbot python3-certbot-nginx curl
fi
if [ -L "$site" ] || { [ -e "$enabled" ] && [ ! -L "$enabled" ]; }; then
    echo "Đường dẫn gki-bot đang được dùng theo cách khác; dừng để giữ cấu hình hiện có." >&2
    exit 1
fi
if [ -L "$enabled" ]; then
    if [ "$(readlink -f "$enabled")" != "$site" ]; then
        echo "$enabled đang trỏ tới site khác; hãy kiểm tra trước." >&2; exit 1
    fi
    had_enabled=1
fi
if [ -f "$site" ]; then
    as_root mkdir -p /etc/nginx/gki-bot-backups
    previous="/etc/nginx/gki-bot-backups/$(date +%Y%m%d-%H%M%S)-$$.conf"
    as_root cp -- "$site" "$previous"
fi

as_root mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled "$acme_root/.well-known/acme-challenge"
render_args=(nginx "$domain" --port "$port")
if [ -s /proc/net/if_inet6 ]; then render_args+=(--ipv6); fi
# Reuse an existing certificate while renewing, avoiding an HTTPS downgrade.
if as_root test -f "/etc/letsencrypt/live/$domain/fullchain.pem" && as_root test -f "/etc/letsencrypt/live/$domain/privkey.pem"; then
    helper "${render_args[@]}" --https > "$candidate"
else
    helper "${render_args[@]}" > "$candidate"
fi
as_root install -m 644 "$candidate" "$site"
as_root ln -sfn "$site" "$enabled"
as_root nginx -t
as_root systemctl enable --now nginx
as_root systemctl reload nginx
if command -v ufw >/dev/null 2>&1 && as_root ufw status | grep -q 'Status: active'; then
    as_root ufw allow 80/tcp
    as_root ufw allow 443/tcp
fi

# Verify the ACME route locally and against EVERY DNS address, not just one.
printf '%s' "$probe" | as_root tee "$challenge" >/dev/null
check_challenge() {
    local address="$1" result
    if [[ "$address" == *:* ]]; then address="[$address]"; fi
    result="$(curl --noproxy '*' --fail --silent --show-error --connect-timeout 5 --max-time 10 \
        --resolve "$domain:80:$address" "http://$domain/.well-known/acme-challenge/$probe")" || return 1
    [ "$result" = "$probe" ]
}
if ! check_challenge 127.0.0.1; then
    echo "Nginx chưa phục vụ đúng domain/ACME. Kiểm tra site trùng server_name bằng sudo nginx -T." >&2; exit 1
fi
while IFS= read -r address; do
    if ! check_challenge "$address"; then
        echo "DNS $domain -> $address không trả đúng ACME challenge. Sửa/xóa bản ghi sai rồi chạy lại." >&2
        exit 1
    fi
done <<< "$addresses"

as_root mkdir -p /etc/letsencrypt/renewal-hooks/deploy
as_root install -m 755 deploy/renew-nginx.sh /etc/letsencrypt/renewal-hooks/deploy/gki-bot-reload
cert_args=(certonly --webroot --webroot-path "$acme_root" --preferred-challenges http \
    --cert-name "$domain" -d "$domain" --keep-until-expiring --non-interactive --agree-tos)
if [ -n "$email" ]; then cert_args+=(--email "$email"); else cert_args+=(--register-unsafely-without-email); fi
as_root certbot "${cert_args[@]}"
helper "${render_args[@]}" --https > "$candidate"
as_root install -m 644 "$candidate" "$site"
as_root nginx -t
as_root systemctl reload nginx

helper env "$domain" --email "$email"
env_changed=1
pm2 startOrRestart deploy/ecosystem.config.cjs --only gki-bot --update-env
pm2 save

echo "Đang chờ bot/dashboard khởi động..."
ready=0
for attempt in {1..30}; do
    if curl --noproxy '*' --fail --silent --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null; then ready=1; break; fi
    sleep 2
done
if [ "$ready" -ne 1 ]; then echo "Backend chưa sẵn sàng; xem bash run.sh logs." >&2; exit 1; fi
if ! curl --noproxy '*' --fail --silent --show-error --max-time 15 \
        --resolve "$domain:443:127.0.0.1" "https://$domain/" >/dev/null; then
    echo "Kiểm tra HTTPS qua Nginx thất bại." >&2; exit 1
fi
if as_root systemctl cat certbot.timer >/dev/null 2>&1; then
    as_root systemctl enable --now certbot.timer
elif [ ! -f /etc/cron.d/certbot ]; then
    echo "Lưu ý: chưa thấy lịch tự gia hạn. Hãy cấu hình chạy sudo certbot renew định kỳ." >&2
fi
echo "Web đã sẵn sàng: https://$domain"
echo "Đã lưu DASHBOARD_URL, bind backend vào localhost và sao lưu .env tại .env.bak."
