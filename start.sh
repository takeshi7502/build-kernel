#!/usr/bin/env bash
# Bootstrap the single Telegram bot on Ubuntu/Debian. For daily operations use run.sh.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
trap 'code=$?; echo "start.sh failed at line $LINENO (exit $code)." >&2; exit "$code"' ERR

CONFIGURE_SSL=0
DOMAIN=""
usage() {
    cat <<'EOF'
Usage: bash start.sh [bot] [--ssl] [--domain kernel.example.com]
Install dependencies, build the dashboard, validate .env and start gki-bot.
The optional 'bot' argument is accepted for compatibility.
--ssl configures the dashboard domain, Nginx and HTTPS after the bot starts.
No arguments in a terminal opens a quick setup menu.
EOF
}

if [ "$#" -eq 0 ] && [ -t 0 ]; then
    printf '\nThiết lập GKI bot\n  1) Cài/khởi động bot\n  2) Cài bot + cấu hình domain, Nginx, HTTPS\n'
    read -r -p "Lựa chọn [1]: " setup_choice
    case "${setup_choice:-1}" in
        1) ;;
        2) CONFIGURE_SSL=1 ;;
        *) echo "Lựa chọn không hợp lệ." >&2; exit 2 ;;
    esac
fi
while [ "$#" -gt 0 ]; do
    case "$1" in
        bot) ;;
        --ssl) CONFIGURE_SSL=1 ;;
        --domain)
            if [ "$#" -lt 2 ]; then echo "Thiếu domain sau --domain." >&2; exit 2; fi
            DOMAIN="$2"; CONFIGURE_SSL=1; shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

if [ ! -f .env ]; then
    echo "Missing .env. Copy .env.example to .env and fill in the required values." >&2
    exit 1
fi

as_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

ensure_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        as_root apt-get update
        as_root apt-get install -y "$2"
    fi
}

ensure_runtime() {
    ensure_command python3 python3
    ensure_command npm npm
    python3 -c 'import sys; sys.exit("Python 3.10 or newer is required.") if sys.version_info < (3, 10) else None'
    node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 18 || (major === 18 && minor < 12)) { console.error("Node.js 18.12 or newer is required to build the dashboard. Install a supported Node.js LTS and retry."); process.exit(1); }'
    if ! command -v pm2 >/dev/null 2>&1; then
        as_root npm install -g pm2
    fi

    if [ -d venv ] && { [ ! -x venv/bin/python ] || ! venv/bin/python -m pip --version >/dev/null 2>&1; }; then
        local backup_name
        backup_name="venv.incomplete.$(date +%Y%m%d%H%M%S).$$"
        echo "Preserving incomplete virtual environment as $backup_name"
        mv -- venv "$backup_name"
    fi

    if [ ! -x venv/bin/python ]; then
        # Importing venv alone does not detect Ubuntu installations without ensurepip.
        as_root apt-get update
        as_root apt-get install -y python3-venv
        python3 -m venv venv
    fi
    venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
}

ensure_dashboard() {
    echo "Building dashboard..."
    (
        cd web
        npm ci --no-audit --no-fund
        npm run build
    )
    # Fresh clones do not contain the ignored catalog JSON. Never replace data.
    mkdir -p web/data/{android12,android13,android14,android15,android16}
    local file
    for file in android12/5.10.json android13/5.15.json android14/6.1.json android15/6.6.json android16/6.12.json; do
        if [ ! -e "web/data/$file" ]; then
            (set -o noclobber; printf '%s\n' '{"entries":[]}' > "web/data/$file")
        fi
    done
    if [ ! -e web/data/announcement.json ]; then
        (set -o noclobber; printf '%s\n' '{"items":[]}' > web/data/announcement.json)
    fi
}


ensure_runtime
# Do not source .env as executable shell code, or contact Telegram during validation.
venv/bin/python -c 'import bot.config'

if pm2 describe gki-userbot >/dev/null 2>&1; then
    echo "Legacy gki-userbot is still registered. Run: bash run.sh migrate" >&2
    echo "No existing PM2 process has been changed." >&2
    exit 1
fi

ensure_dashboard
pm2 startOrRestart deploy/ecosystem.config.cjs --only gki-bot --update-env
pm2 save

# DNS/ACME failure must not prevent an otherwise valid bot from starting.
if [ "$CONFIGURE_SSL" -eq 1 ]; then
    if [ -n "$DOMAIN" ]; then bash scripts/configure_web.sh "$DOMAIN"; else bash scripts/configure_web.sh; fi
fi

pm2 status gki-bot
echo "PM2 start requested. Check startup and connection status: bash run.sh logs"
echo "For reboot startup, run 'pm2 startup' and follow the command it prints, then 'pm2 save'."
