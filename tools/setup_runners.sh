#!/usr/bin/env bash
# Optional GitHub Actions self-hosted runner setup. Independent from Telegram bot.
set -Eeuo pipefail

usage() {
    cat <<'HELP'
Cách dùng:
  bash tools/setup_runners.sh install OWNER/REPO VERSION [BASE_DIR]
  bash tools/setup_runners.sh remove RUNNER_DIR

Linux x86_64; VERSION là bản runner lấy từ GitHub Settings -> Actions -> Runners.
BASE_DIR mặc định: ./runners. Mỗi lần install thêm một runner, không ghi đè runner cũ.
Token được hỏi kín (hoặc truyền bằng GITHUB_RUNNER_TOKEN / GITHUB_RUNNER_REMOVAL_TOKEN).
HELP
}

fail() { printf 'Lỗi: %s\n' "$*" >&2; exit 1; }
service() {
    if [[ "$EUID" -eq 0 ]]; then ./svc.sh "$@"; else sudo ./svc.sh "$@"; fi
}

case "${1:-help}" in
    help|-h|--help) usage; exit 0 ;;
    install)
        [[ "$#" -ge 3 && "$#" -le 4 ]] || { usage; exit 2; }
        [[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || fail 'Chỉ hỗ trợ Linux x86_64.'
        [[ "$2" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'Repository phải có dạng OWNER/REPO.'
        [[ "$3" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Version không hợp lệ.'
        REPOSITORY="$2"
        VERSION="$3"
        BASE_DIR="${4:-./runners}"
        mkdir -p -- "$BASE_DIR"
        BASE_DIR="$(cd -- "$BASE_DIR" && pwd -P)"
        [[ "$BASE_DIR" != / ]] || fail 'Không dùng / làm thư mục runner.'
        TOKEN="${GITHUB_RUNNER_TOKEN:-}"
        if [[ -z "$TOKEN" ]]; then
            read -r -s -p 'Registration token từ GitHub: ' TOKEN
            printf '\n'
        fi
        [[ -n "$TOKEN" ]] || fail 'Registration token không được trống.'
        RUNNER_DIR="$(mktemp -d "$BASE_DIR/runner-XXXXXXXX")"
        ARCHIVE="$RUNNER_DIR/runner.tar.gz"
        curl --fail --show-error --location --retry 3 \
            "https://github.com/actions/runner/releases/download/v${VERSION}/actions-runner-linux-x64-${VERSION}.tar.gz" \
            --output "$ARCHIVE"
        tar xzf "$ARCHIVE" -C "$RUNNER_DIR"
        cd -- "$RUNNER_DIR"
        [[ "$EUID" -ne 0 ]] || export RUNNER_ALLOW_RUNASROOT=1
        ./config.sh --url "https://github.com/$REPOSITORY" --token "$TOKEN" \
            --name "$(hostname)-$(basename "$RUNNER_DIR")" --unattended
        unset TOKEN
        service install
        service start
        printf 'Runner đã cài tại: %s\n' "$RUNNER_DIR"
        ;;
    remove)
        [[ "$#" -eq 2 ]] || { usage; exit 2; }
        RUNNER_DIR="$(cd -- "$2" && pwd -P)"
        [[ "$RUNNER_DIR" != / && -f "$RUNNER_DIR/.runner" && -f "$RUNNER_DIR/config.sh" && -f "$RUNNER_DIR/svc.sh" ]] \
            || fail 'Đường dẫn phải trỏ đúng một runner đã đăng ký.'
        TOKEN="${GITHUB_RUNNER_REMOVAL_TOKEN:-}"
        if [[ -z "$TOKEN" ]]; then
            read -r -s -p 'Removal token từ GitHub: ' TOKEN
            printf '\n'
        fi
        [[ -n "$TOKEN" ]] || fail 'Removal token không được trống.'
        cd -- "$RUNNER_DIR"
        [[ "$EUID" -ne 0 ]] || export RUNNER_ALLOW_RUNASROOT=1
        service stop
        service uninstall
        ./config.sh remove --token "$TOKEN"
        unset TOKEN
        printf 'Đã hủy đăng ký runner. Giữ lại thư mục để bạn tự kiểm tra/xóa: %s\n' "$RUNNER_DIR"
        ;;
    *) usage; exit 2 ;;
esac
