#!/bin/bash

# ==========================================
# SCRIPT TỰ ĐỘNG TẠO NHIỀU RUNNER TRÊN 1 VPS
# Cảnh báo: Chạy 10 job build kernel song song trên 1 VPS 8 Core có thể làm sập máy (OOM)!
# ==========================================

URL="https://github.com/takeshi7502/GKI_KernelSU_SUSFS"
TOKEN="AZRJC5OZMEPBOIDLZRNNOP3JXWMSK"
RUNNER_COUNT=10
RUNNER_VERSION="2.332.0"
TAR_FILE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"

# 1. Tải bộ cài runner (nếu chưa có)
if [ ! -f "$TAR_FILE" ]; then
    echo "📥 Đang tải Github Actions Runner v${RUNNER_VERSION}..."
    curl -o $TAR_FILE -L "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TAR_FILE}"
fi

# 2. Vòng lặp tạo 10 con runner
for i in $(seq 1 $RUNNER_COUNT); do
    DIR="runner-$i"
    echo "========================================="
    echo "🚀 Đang setup $DIR..."
    echo "========================================="
    
    # Tạo thư mục và giải nén (nếu thư mục chưa rỗng thì xoá sạch tạo lại)
    rm -rf $DIR
    mkdir $DIR
    tar xzf ./$TAR_FILE -C $DIR
    
    cd $DIR
    
    # Cấu hình tự động (không hỏi yes/no)
    ./config.sh --url $URL --token $TOKEN --name "VPS-Runner-$i" --unattended --replace
    
    # Cài đặt thành service chạy ngầm của Linux và Khởi động
    sudo ./svc.sh install
    sudo ./svc.sh start
    
    cd ..
    echo "✅ $DIR đã chạy ngầm thành công!"
done

echo ""
echo "🎉 XONG! Đã tạo và cắm 10 con runner chạy ngầm trên VPS."
echo "Hãy kiểm tra trên Github, bạn sẽ thấy 10 con VPS-Runner-1 đến VPS-Runner-10 sáng đèn xanh."
