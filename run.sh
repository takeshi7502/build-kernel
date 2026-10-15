#!/usr/bin/env bash
# Daily operations for one Telegram bot. Never reset local Git changes or data.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
trap 'code=$?; echo "run.sh failed at line $LINENO (exit $code)." >&2; exit "$code"' ERR

usage() {
    cat <<'EOF'
Usage: bash run.sh [command] [bot]
       bash run.sh web [kernel.example.com]
  status    Show the bot's PM2 status
  logs      Follow the bot's logs
  restart   Validate config and restart the bot
  update    Fetch and fast-forward code; no install or restart
  deploy    Safe update, install dependencies, build dashboard and restart
  migrate   Remove the legacy gki-userbot PM2 entry; preserve all data files
  web       Cấu hình domain, Nginx, HTTPS và link dashboard tự động
No command opens the interactive menu. Optional 'bot' is backward compatible.
EOF
}

require_pm2() {
    if ! command -v pm2 >/dev/null 2>&1; then
        echo "PM2 is missing. Run: bash start.sh" >&2
        exit 1
    fi
}

restart_bot() {
    require_pm2
    if [ ! -x venv/bin/python ]; then
        echo "Virtual environment is missing. Run: bash start.sh" >&2
        return 1
    fi
    venv/bin/python -c 'import bot.config'
    if pm2 describe gki-userbot >/dev/null 2>&1; then
        echo "Run 'bash run.sh migrate' to retire the legacy process first." >&2
        return 1
    fi
    pm2 startOrRestart deploy/ecosystem.config.cjs --only gki-bot --update-env
    pm2 save
}

safe_update() {
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "This directory is not a Git repository." >&2
        return 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "Local changes found. Commit or stash them before updating." >&2
        git status --short
        return 1
    fi
    local branch
    branch="$(git branch --show-current)"
    if [ -z "$branch" ]; then
        echo "Cannot update a detached HEAD. Check out your deployment branch first." >&2
        return 1
    fi
    git fetch --prune origin "$branch"
    git merge --ff-only "origin/$branch"
}

migrate_legacy_process() {
    require_pm2
    if pm2 describe gki-userbot >/dev/null 2>&1; then
        echo "Removing the retired gki-userbot PM2 entry. Data and session files are preserved."
        pm2 delete gki-userbot
        pm2 save
    else
        echo "No legacy PM2 entry found. Nothing to migrate."
    fi
}

interactive_menu() {
    local choice
    printf '\nGKI bot\n  1) Status\n  2) Logs\n  3) Restart\n  4) Update code\n  5) Deploy\n  6) Migrate legacy PM2 entry\n  7) Cấu hình domain + Nginx + HTTPS\n'
    read -r -p "Choice [1]: " choice
    case "${choice:-1}" in
        1) command_name=status ;;
        2) command_name=logs ;;
        3) command_name=restart ;;
        4) command_name=update ;;
        5) command_name=deploy ;;
        6) command_name=migrate ;;
        7) command_name=web ;;
        *) echo "Invalid choice." >&2; exit 2 ;;
    esac
}

if [ "$#" -gt 2 ] || { [ "$#" -eq 2 ] && [ "${1:-}" != web ] && [ "$2" != bot ]; }; then
    usage >&2
    exit 2
fi
command_name="${1:-}"
if [ -z "$command_name" ]; then
    interactive_menu
fi

case "$command_name" in
    -h|--help|help) usage ;;
    status) require_pm2; pm2 status gki-bot ;;
    logs) require_pm2; pm2 logs gki-bot ;;
    restart) restart_bot ;;
    update) safe_update; echo "Code updated. Run 'bash start.sh' to install/build and start it." ;;
    deploy) safe_update; bash start.sh ;;
    migrate) migrate_legacy_process ;;
    web)
        if [ -n "${2:-}" ]; then bash scripts/configure_web.sh "$2"; else bash scripts/configure_web.sh; fi
        ;;
    *) echo "Unknown command: $command_name" >&2; usage; exit 2 ;;
esac
