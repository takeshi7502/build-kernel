#!/bin/bash

echo ""
echo "================================================="
echo "       GKI BOT - QUẢN LÝ NHANH (run.sh)         "
echo "================================================="
echo ""
echo "  [1] Pull code mới + Restart tất cả"
echo "  [2] Chỉ Pull code mới (không restart)"
echo "  [3] Pull + Restart gki-bot"
echo "  [4] Pull + Restart gki-userbot"
echo "  [5] Chỉ Restart gki-bot"
echo "  [6] Chỉ Restart gki-userbot"
echo "  [7] Xem trạng thái các tiến trình (pm2 list)"
echo "  [8] Xem Log gki-bot (Ctrl+C để thoát)"
echo "  [9] Xem Log gki-userbot (Ctrl+C để thoát)"
echo "  [0] Dọn rác Log cũ (pm2 flush)"
echo ""
read -p "Nhập lựa chọn của bạn: " choice
echo ""

_pull() {
    echo "📥 Đang kéo code mới nhất từ GitHub..."
    git fetch --all && git reset --hard origin/main
    echo "✅ Pull xong!"
    echo ""
}

_restart_bot() {
    echo "🔄 Đang restart gki-bot..."
    pm2 restart gki-bot
}

_restart_userbot() {
    echo "🔄 Đang restart gki-userbot..."
    pm2 restart gki-userbot
}

case "$choice" in
    1)
        _pull
        echo "🔄 Đang restart tất cả tiến trình..."
        pm2 restart all
        ;;
    2)
        _pull
        echo "ℹ️  Code đã được cập nhật. Các tiến trình chưa được restart."
        ;;
    3)
        _pull
        _restart_bot
        ;;
    4)
        _pull
        _restart_userbot
        ;;
    5)
        _restart_bot
        ;;
    6)
        _restart_userbot
        ;;
    7)
        pm2 list
        ;;
    8)
        echo "📋 Đang xem log gki-bot (Nhấn Ctrl+C để thoát)..."
        pm2 logs gki-bot
        ;;
    9)
        echo "📋 Đang xem log gki-userbot (Nhấn Ctrl+C để thoát)..."
        pm2 logs gki-userbot
        ;;
    0)
        echo "🗑️  Đang dọn rác log cũ..."
        pm2 flush
        echo "✅ Đã xoá trắng log!"
        ;;
    *)
        echo "❌ Lựa chọn không hợp lệ."
        exit 1
        ;;
esac

echo ""
pm2 list
echo ""
echo "================================================="
