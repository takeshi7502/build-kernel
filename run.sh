#!/usr/bin/env bash
# Daily operations for GKI bot services. This script never discards Git work
# unless the explicit force-update command is confirmed.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

trap 'code=$?; echo ""; echo "❌ run.sh dừng ở dòng $LINENO (mã lỗi: $code)."; exit "$code"' ERR

usage() {
    cat <<'EOF'
Cách dùng:
  bash run.sh [command] [target]

Commands:
  status                         Xem trạng thái GKI services
  logs [bot|userbot]             Xem log (mặc định bot)
  restart [bot|userbot|all]      Restart các service GKI
  update                         Update an toàn, không restart
  deploy [bot|userbot|all]       Update an toàn rồi restart target (mặc định all)
  flush                          Xoá log của riêng GKI services
  force-update                   Reset cứng về origin/<branch> (cần xác nhận RESET)

Không truyền command sẽ hiện menu tương tác.
EOF
}

process_exists() {
    pm2 describe "$1" >/dev/null 2>&1
}

require_pm2() {
    if ! command -v pm2 >/dev/null 2>&1; then
        echo "❌ Không tìm thấy PM2. Hãy chạy: bash start.sh"
        exit 1
    fi
}

restart_one() {
    local name="$1"
    if process_exists "$name"; then
        echo "🔄 Restart $name..."
        pm2 restart "$name" --update-env
    else
        echo "⚠️  $name chưa được PM2 quản lý; bỏ qua."
    fi
}

restart_target() {
    require_pm2
    case "${1:-all}" in
        bot) restart_one gki-bot ;;
        userbot) restart_one gki-userbot ;;
        all)
            restart_one gki-bot
            restart_one gki-userbot
            ;;
        *) echo "❌ Target phải là bot, userbot hoặc all."; exit 2 ;;
    esac
    pm2 save
}

show_status() {
    require_pm2
    pm2 status gki-bot gki-userbot || true
}

show_logs() {
    require_pm2
    case "${1:-bot}" in
        bot) pm2 logs gki-bot ;;
        userbot) pm2 logs gki-userbot ;;
        *) echo "❌ Target log phải là bot hoặc userbot."; exit 2 ;;
    esac
}

sync_dependencies_if_needed() {
    local before_sha="$1"
    local changed
    changed="$(git diff --name-only "$before_sha" HEAD)"

    if grep -qx 'requirements.txt' <<<"$changed"; then
        if [ ! -x "venv/bin/pip" ]; then
            echo "❌ requirements.txt đã đổi nhưng thiếu venv. Hãy chạy: bash start.sh"
            exit 1
        fi
        echo "📦 requirements.txt thay đổi; đang đồng bộ dependencies..."
        ./venv/bin/pip install --disable-pip-version-check -q -r requirements.txt
    fi

    if grep -Eq '^(web/(js|scss)/|web/(package.json|package-lock.json|webpack.config.js))' <<<"$changed"; then
        if [ ! -f "web/dist/app.bundle.js" ] || [ ! -f "web/dist/style.bundle.css" ]; then
            echo "⚠️  Frontend source đã đổi nhưng web/dist đang thiếu. Chạy: (cd web && npm ci && npm run build)"
        else
            echo "ℹ️  Frontend source đã đổi; bundle web/dist từ repository sẽ được dùng."
        fi
    fi
}

safe_update() {
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "❌ Thư mục này không phải Git repository."
        exit 1
    fi
    if [ -n "$(git status --porcelain)" ]; then
        echo "❌ Có thay đổi local; update bị dừng để tránh mất dữ liệu."
        git status --short
        echo "   Commit/stash thay đổi, hoặc chỉ khi chắc chắn hãy dùng: bash run.sh force-update"
        return 1
    fi

    local branch before_sha
    branch="$(git branch --show-current)"
    if [ -z "$branch" ]; then
        echo "❌ HEAD đang detached; không thể update an toàn."
        return 1
    fi
    before_sha="$(git rev-parse HEAD)"

    echo "📥 Đang cập nhật origin/$branch (fast-forward only)..."
    git fetch --prune origin "$branch"
    git merge --ff-only "origin/$branch"

    if [ "$(git rev-parse HEAD)" = "$before_sha" ]; then
        echo "✅ Code đã là mới nhất."
        return 0
    fi

    sync_dependencies_if_needed "$before_sha"
    echo "✅ Update hoàn tất."
}

force_update() {
    local branch
    branch="$(git branch --show-current)"
    if [ -z "$branch" ]; then
        echo "❌ HEAD đang detached; không thể force update."
        exit 1
    fi
    echo "⚠️  Thao tác này xoá toàn bộ thay đổi local và reset về origin/$branch."
    read -r -p "Gõ RESET để tiếp tục: " confirmation
    if [ "$confirmation" != "RESET" ]; then
        echo "Đã huỷ."
        return 0
    fi
    git fetch --prune origin "$branch"
    git reset --hard "origin/$branch"
    echo "✅ Đã reset về origin/$branch."
}

flush_logs() {
    require_pm2
    for name in gki-bot gki-userbot; do
        if process_exists "$name"; then
            pm2 flush "$name"
        fi
    done
    echo "✅ Đã dọn log của GKI services."
}

interactive_menu() {
    echo ""
    echo "================================================="
    echo "       GKI BOT - QUẢN LÝ AN TOÀN                "
    echo "================================================="
    echo "  [1] Update an toàn + restart GKI services"
    echo "  [2] Chỉ update an toàn"
    echo "  [3] Update + restart gki-bot"
    echo "  [4] Update + restart gki-userbot"
    echo "  [5] Restart GKI services"
    echo "  [6] Restart gki-bot"
    echo "  [7] Restart gki-userbot"
    echo "  [8] Xem trạng thái"
    echo "  [9] Xem log gki-bot"
    echo "  [10] Xem log gki-userbot"
    echo "  [11] Dọn log GKI services"
    echo "  [12] Force update (xoá thay đổi local, cần xác nhận)"
    echo ""
    read -r -p "Lựa chọn: " choice
    case "$choice" in
        1) safe_update && restart_target all ;;
        2) safe_update ;;
        3) safe_update && restart_target bot ;;
        4) safe_update && restart_target userbot ;;
        5) restart_target all ;;
        6) restart_target bot ;;
        7) restart_target userbot ;;
        8) show_status ;;
        9) show_logs bot ;;
        10) show_logs userbot ;;
        11) flush_logs ;;
        12) force_update ;;
        *) echo "❌ Lựa chọn không hợp lệ."; exit 2 ;;
    esac
}

case "${1:-}" in
    "") interactive_menu ;;
    -h|--help|help) usage ;;
    status) show_status ;;
    logs) show_logs "${2:-bot}" ;;
    restart) restart_target "${2:-all}" ;;
    update) safe_update ;;
    deploy)
        safe_update
        restart_target "${2:-all}"
        ;;
    flush) flush_logs ;;
    force-update) force_update ;;
    *) echo "❌ Command không hợp lệ: $1"; usage; exit 2 ;;
esac
