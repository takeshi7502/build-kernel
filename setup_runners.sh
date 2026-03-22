#!/bin/bash
# ==========================================
# SCRIPT TỰ ĐỘNG TẠO NHIỀU RUNNER TRÊN 1 VPS
# Có tính năng tự động phát hiện mã Token chết và gợi ý an toàn chống Sập Nguồn (OOM)
# ==========================================

# 1. Tải cấu hình từ .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

URL="https://github.com/${GITHUB_OWNER}/${GKI_REPO}"
ENV_TOKEN="${GITHUB_RUNNER_TOKEN}"

# Hàm nhập token
get_token() {
    while true; do
        read -p "🔑 Nhập GITHUB_RUNNER_TOKEN mới (Nhấn Enter để xài token cũ trong .env): " INPUT_TOKEN
        FINAL_TOKEN="${INPUT_TOKEN:-$ENV_TOKEN}"
        
        if [ -z "$FINAL_TOKEN" ]; then
            echo "❌ LỖI: Token không được để trống! Cố gắng tìm lại trong Github nhé."
        else
            # Cập nhật ngược lại vào ENV_TOKEN để lần chạy sau lỡ lỗi thì gợi ý luôn
            ENV_TOKEN="$FINAL_TOKEN"
            break
        fi
    done
}

echo "========================================="
echo "💻 MENU QUẢN LÝ GITHUB ACTIONS"
echo "========================================="
echo "1. Cài đặt Runner mới"
echo "2. Gỡ bỏ Runner hiện tại"
echo "========================================="
read -p "👉 Chọn chức năng [1/2, Nhấn Enter mặc định là 1]: " MENU_OPTION
MENU_OPTION=${MENU_OPTION:-1}

if [ "$MENU_OPTION" = "2" ]; then
    echo ""
    echo "========================================="
    echo "🗑️ GỠ BỎ RUNNER ĐANG CHẠY"
    echo "========================================="
    read -p "🔢 Bạn muốn gỡ bỏ bao nhiêu Runner? [Mặc định: 1]: " INPUT_REMOVE
    REMOVE_COUNT=${INPUT_REMOVE:-1}
    
    if ! [[ "$REMOVE_COUNT" =~ ^[0-9]+$ ]]; then
        echo "❌ LỖI: Vui lòng gõ một con số hợp lệ!"
        exit 1
    fi
    
    get_token
    VPS_NAME=$(hostname)
    i=1
    while [ $i -le $REMOVE_COUNT ]; do
        DIR="runner-$i"
        if [ -d "$DIR" ]; then
            echo "🛑 Đang gỡ bỏ ${VPS_NAME}-Runner-$i..."
            cd $DIR
            sudo ./svc.sh stop
            sudo ./svc.sh uninstall
            ./config.sh remove --token "$FINAL_TOKEN"
            cd ..
            rm -rf $DIR
            echo "✅ Gỡ bỏ thành công ${VPS_NAME}-Runner-$i!"
        else
            echo "⚠️ Thư mục $DIR không tồn tại. Bỏ qua."
        fi
        i=$((i + 1))
    done
    echo "🎉 Đã hoàn tất gỡ bỏ dưới máy ảo VPS!"
    
    if [ -n "$GITHUB_TOKEN" ] && [ -n "$GITHUB_OWNER" ] && [ -n "$GKI_REPO" ]; then
        echo ""
        echo "🧹 BẮT ĐẦU CÀN QUÉT BÓNG MA (ZOMBIE RUNNERS) TRÊN GITHUB..."
        echo "Đang liên lạc với máy chủ mẹ Github API để quét mã số ID..."
        OFFLINE_IDS=$(curl -s -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/vnd.github.v3+json" "https://api.github.com/repos/${GITHUB_OWNER}/${GKI_REPO}/actions/runners" | grep -B 3 '"status": "offline"' | grep '"id":' | grep -o '[0-9]\+')
        
        if [ -z "$OFFLINE_IDS" ]; then
            echo "✅ Không phát hiện thấy cái xác thối Zombie nào trên Github!"
        else
            for RUNNER_ID in $OFFLINE_IDS; do
                echo "🔥 Đang phóng hoả tiêu huỷ xác Zombie Runner ID: $RUNNER_ID..."
                curl -s -X DELETE -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/vnd.github.v3+json" "https://api.github.com/repos/${GITHUB_OWNER}/${GKI_REPO}/actions/runners/$RUNNER_ID"
            done
            echo "✨ SẠCH BÓNG KHÔNG CÒN TÌ VẾT! Danh sách Runner trên Github giờ đã trống trơn!"
        fi
    fi
    exit 0
fi

echo ""
echo "========================================="
echo "💻 KIỂM TRA THÔNG SỐ SERVER VPS..."
echo "========================================="
CORES=$(nproc)
RAM_MB=$(free -m | awk '/Mem:/ { print $2 }')
RAM_GB=$((RAM_MB / 1024))

# Cứ mỗi Core gánh 1 Runner, nhưng nếu RAM ít hơn 4GB cho 1 Core thì RAM là điểm yếu
# Build Kernel tốn cực kỳ nhiều RAM, khuyến nghị 1 Runner / 3GB RAM tối thiểu
MAX_BY_RAM=$((RAM_GB / 3))
if [ "$MAX_BY_RAM" -lt 1 ]; then MAX_BY_RAM=1; fi

SUGGESTED_RUNNERS=$(( CORES < MAX_BY_RAM ? CORES : MAX_BY_RAM ))
if [ "$SUGGESTED_RUNNERS" -lt 1 ]; then SUGGESTED_RUNNERS=1; fi

echo "- CPU Cores: $CORES"
echo "- RAM: $RAM_GB GB"
echo "- 🛠️ Số lượng Runner Tối Đa Khuyến Nghị: $SUGGESTED_RUNNERS (Để hạn chế rủi ro sập nguồn OOM)"
echo "-----------------------------------------"

read -p "🔢 Bạn muốn tạo bao nhiêu Runner chạy song song? [Mặc định: $SUGGESTED_RUNNERS]: " INPUT_COUNT
RUNNER_COUNT=${INPUT_COUNT:-$SUGGESTED_RUNNERS}

if ! [[ "$RUNNER_COUNT" =~ ^[0-9]+$ ]]; then
    echo "❌ LỖI: Vui lòng gõ một con số hợp lệ!"
    exit 1
fi

echo ""
get_token

RUNNER_VERSION="2.332.0"
TAR_FILE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"

if [ ! -f "$TAR_FILE" ]; then
    echo "📥 Đang tải Github Actions Runner v${RUNNER_VERSION}..."
    curl -o $TAR_FILE -L "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TAR_FILE}"
fi

export RUNNER_ALLOW_RUNASROOT=1
VPS_NAME=$(hostname)

i=1
while [ $i -le $RUNNER_COUNT ]; do
    DIR="runner-$i"
    echo ""
    echo "========================================="
    echo "🚀 Đang setup ${VPS_NAME}-Runner-$i..."
    echo "========================================="
    
    rm -rf $DIR
    mkdir -p $DIR
    tar xzf ./$TAR_FILE -C $DIR
    
    cd $DIR
    
    # Chạy config và hứng mã lỗi
    if ! ./config.sh --url "$URL" --token "$FINAL_TOKEN" --name "${VPS_NAME}-Runner-$i" --unattended --replace; then
        echo ""
        echo "🚨 LỖI GITHUB TỪ CHỐI (Mã 404/401)!"
        echo "Nguyên nhân 99% là do TOKEN bạn nhập đã cũ hoặc hết hạn 1 tiếng."
        cd ..
        rm -rf $DIR
        
        # Bắt nhập lại token mới
        get_token
        # Quay lại đầu vòng lặp để tạo lại đúng con runner thứ i này
        continue
    fi
    
    sudo ./svc.sh install
    sudo ./svc.sh start
    
    cd ..
    echo "✅ ${VPS_NAME}-Runner-$i đã cài cắm thành công và cày cuốc ngầm 24/7!"
    
    # Nhích lên runner tiếp theo
    i=$((i + 1))
done

echo ""
echo "🎉 XUẤT SẮC! Cả $RUNNER_COUNT con runner đã chạy xong!"
echo "Hãy về lại giao diện Github -> Settings -> Actions -> Runners để ngắm dàn máy cày xanh lè!"
