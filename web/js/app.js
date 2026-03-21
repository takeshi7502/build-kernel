// CẤU HÌNH KẾT NỐI (Hãy đổi theo đúng thông tin .env của bạn)
const GITHUB_OWNER = "takeshi7502";           // Thay bằng tên Github của bạn (VD: zzh20188)
const GKI_REPO = "GKI_KernelSU_SUSFS";  // Tên kho Kernel Source của bạn

// Tự động tạo link lấy dữ liệu (KHÔNG CẦN SỬA)
const DATA_URL = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GKI_REPO}/web-data/web_data.json`;

let currentBuilds = [];

// Hàm tạo mã HTML cho từng Card
function createBuildCard(build) {
    // Xác định Status class & label
    let statusClass = build.status;
    let statusLabel = build.status === 'success' ? 'Hoàn thành' 
                    : build.status === 'building' ? 'Đang Build' 
                    : 'Lỗi';
    
    // Nút download tuỳ theo trạng thái
    let actionButtons = '';
    if (build.status === 'success') {
        actionButtons = `
            <a href="${build.dl_link}" class="btn btn-primary"><i class="fa-solid fa-download"></i> Tải Kernel</a>
            <button class="btn btn-secondary" title="Copy Link"><i class="fa-regular fa-copy"></i></button>
        `;
    } else if (build.status === 'building') {
        actionButtons = `
            <button class="btn btn-primary" disabled><i class="fa-solid fa-spinner fa-spin"></i> Đang biên dịch...</button>
        `;
    } else {
        actionButtons = `
            <button class="btn btn-secondary"><i class="fa-solid fa-terminal"></i> Xem Logs Lỗi</button>
        `;
    }

    return `
        <div class="build-card" data-status="${build.status}">
            <div class="card-header">
                <div>
                    <h3 class="card-title">${build.os_version}</h3>
                    <div class="card-date"><i class="fa-regular fa-clock"></i> ${build.date}</div>
                </div>
                <div class="status ${statusClass}">${statusLabel}</div>
            </div>
            
            <div class="specs-list">
                <div class="spec-item">
                    <span class="spec-label">Hệ KernelSU:</span>
                    <span class="spec-value">${build.ksu_version}</span>
                </div>
                <div class="spec-item">
                    <span class="spec-label">SUSFS:</span>
                    <span class="spec-value">${build.susfs_version}</span>
                </div>
                <div class="spec-item">
                    <span class="spec-label">Mã Commit Git:</span>
                    <span class="spec-value"><i class="fa-brands fa-git-alt" style="color:#f14e32"></i> ${build.commit}</span>
                </div>
            </div>

            <div class="card-actions">
                ${actionButtons}
            </div>
        </div>
    `;
}

// Cập nhật trạng thái Bot (Online/Offline)
function updateStatusBadge(botStatus, lastPing) {
    const badge = document.querySelector('.stat-badge');
    
    // Nếu quá 2 phút không ping, coi như offline
    let isOnline = false;
    if (botStatus === "online" && lastPing) {
        const now = Math.floor(Date.now() / 1000);
        if (now - lastPing < 120) {
            isOnline = true;
        }
    }

    if (isOnline) {
        badge.innerHTML = `<span class="pulse-dot"></span> Hệ thống đang Online`;
        badge.style.color = 'var(--status-success)';
        badge.style.background = 'var(--status-bg-success)';
        badge.style.borderColor = 'rgba(16, 185, 129, 0.2)';
    } else {
        badge.innerHTML = `<span class="pulse-dot" style="background-color: var(--status-failed); box-shadow: none; animation: none;"></span> Hệ thống đang Offline`;
        badge.style.color = 'var(--status-failed)';
        badge.style.background = 'var(--status-bg-failed)';
        badge.style.borderColor = 'rgba(239, 68, 68, 0.2)';
    }
}

// Xử lý Render
function renderBuilds(filter = 'all') {
    const container = document.getElementById('builds-container');
    container.innerHTML = '';
    
    if (currentBuilds.length === 0) {
        container.innerHTML = '<p style="color: var(--text-secondary); grid-column: 1 / -1; text-align: center; padding: 40px;">Đang tải dữ liệu hoặc chưa có bản build nào...</p>';
        return;
    }

    currentBuilds.forEach(build => {
        if (filter === 'all' || build.status === filter) {
            container.innerHTML += createBuildCard(build);
        }
    });
}

// Hàm Fetch dữ liệu thật
async function loadData() {
    try {
        const res = await fetch(`${DATA_URL}?t=${new Date().getTime()}`);
        if (!res.ok) throw new Error("Fetch failed");
        const data = await res.json();
        
        currentBuilds = data.builds || [];
        updateStatusBadge(data.status, data.last_ping);
        
        // Giữ nguyên filter đang kích hoạt
        const activeFilter = document.querySelector('.filter-btn.active').dataset.filter;
        renderBuilds(activeFilter);
    } catch (err) {
        console.error("Lỗi lấy dữ liệu:", err);
        updateStatusBadge("offline", 0);
    }
}

// Lắng nghe sự kiện
document.addEventListener('DOMContentLoaded', () => {
    // Load lần đầu tiên
    loadData();

    // Nút lọc
    const filterBtns = document.querySelectorAll('.filter-btn');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            filterBtns.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            renderBuilds(e.target.dataset.filter);
        });
    });

    // Auto Refresh mỗi 15 giây để luôn lấy dữ liệu mới nhất
    setInterval(loadData, 15000);
});
