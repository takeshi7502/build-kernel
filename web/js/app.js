// CẤU HÌNH KẾT NỐI (Nếu chạy Vercel độc lập thì điền link cục bộ, nếu chạy trên VPS thì dùng /api/data)
const DATA_URL = "/api/data";

let currentBuilds = [];

// Hàm format thời gian
function formatDate(isoString) {
    if (!isoString) return "";
    try {
        const d = new Date(isoString);
        const HH = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        const DD = String(d.getDate()).padStart(2, '0');
        const MM = String(d.getMonth() + 1).padStart(2, '0');
        const YYYY = d.getFullYear();
        return `${HH}:${mm} ${DD}-${MM}-${YYYY}`;
    } catch (e) {
        return isoString;
    }
}

// Hàm tạo mã HTML cho từng Card
function createBuildCard(build) {
    // Xác định Status class & label
    let statusClass = build.status;
    let statusLabel = build.status === 'success' ? 'Success'
        : build.status === 'building' ? 'Đang Build'
            : 'Lỗi';

    // Nút action (Download & Github)
    let actionButtons = '';
    if (build.status === 'success') {
        actionButtons = `
            <a href="${build.nightly_link}" target="_blank" class="btn btn-primary" style="flex: 1; text-align: center; text-decoration: none;"><i class="fa-solid fa-download"></i> Download</a>
            <a href="${build.github_link}" target="_blank" class="btn btn-secondary" style="flex: 1; text-align: center; text-decoration: none;"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else if (build.status === 'building') {
        actionButtons = `
            <button class="btn btn-primary" disabled style="flex: 1; opacity: 0.7;"><i class="fa-solid fa-spinner fa-spin"></i> Đang biên dịch</button>
            <a href="${build.github_link}" target="_blank" class="btn btn-secondary" style="flex: 1; text-align: center; text-decoration: none;"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else {
        actionButtons = `
            <button class="btn btn-secondary" disabled style="flex: 1; opacity: 0.7; color: #ef4444; border-color: #ef4444;"><i class="fa-solid fa-xmark"></i> Thất bại</button>
            <a href="${build.github_link}" target="_blank" class="btn btn-secondary" style="flex: 1; text-align: center; text-decoration: none;"><i class="fa-brands fa-github"></i> Github</a>
        `;
    }

    return `
        <div class="build-card" data-status="${build.status}">
            <div class="card-header" style="margin-bottom: 8px;">
                <div style="flex: 1; min-width: 0; overflow: hidden; padding-right: 10px;">
                    <h3 class="card-title" style="margin-bottom: 2px;">${build.title || 'Unknown OS'}</h3>
                    <div style="font-size: 0.9rem; font-weight: 500; color: var(--text-secondary);">${build.sub_title || ''}</div>
                </div>
                <div class="status ${statusClass}" style="flex-shrink: 0;">${statusLabel}</div>
            </div>
            
            <div class="card-date" style="color: var(--text-secondary); font-size: 0.85rem; display: flex; align-items: center; white-space: nowrap;">
                <i class="fa-regular fa-clock" style="margin-right: 5px;"></i> 
                <span style="margin-right: 5px;">${formatDate(build.date)}</span>
                <span style="margin-right: 4px;">by</span>
                <div class="user-name-scroller" style="font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0;">${build.user_name || 'Unknown'}</div>
            </div>
            
            <div class="specs-list" style="margin-top: 15px; margin-bottom: 20px; font-family: 'JetBrains Mono', monospace;">
                <div class="spec-item">
                    <span class="spec-label">Custom version:</span>
                    <span class="spec-value" style="font-weight: 700;">${build.custom_version || '(Mặc định)'}</span>
                </div>
                
                <div class="spec-item" style="display: flex; gap: 15px;">
                    <div style="flex: 1; display: flex; justify-content: space-between;">
                        <span class="spec-label">ZRAM:</span>
                        <span class="spec-value" style="font-weight: 700;">${build.zram}</span>
                    </div>
                    <div style="flex: 1; display: flex; justify-content: space-between;">
                        <span class="spec-label">KPM:</span>
                        <span class="spec-value" style="font-weight: 700;">${build.kpm}</span>
                    </div>
                </div>
                
                <div class="spec-item" style="display: flex; gap: 15px; margin-bottom: 0px;">
                    <div style="flex: 1; display: flex; justify-content: space-between;">
                        <span class="spec-label">BBG:</span>
                        <span class="spec-value" style="font-weight: 700;">${build.bbg}</span>
                    </div>
                    <div style="flex: 1; display: flex; justify-content: space-between;">
                        <span class="spec-label">SUSFS:</span>
                        <span class="spec-value" style="font-weight: 700;">${build.susfs}</span>
                    </div>
                </div>
            </div>

            <div class="card-actions" style="display: flex; gap: 10px;">
                ${actionButtons}
            </div>
        </div>
    `;
}

// Cập nhật trạng thái Bot (Online/Offline)
function updateStatusBadge(botStatus, lastPing) {
    const badge = document.querySelector('.stat-badge');

    // Nếu quá 6.5 phút (400s) không ping, coi như offline
    // (Lý do: Github Raw có bộ đệm Cache mặc định là 5 phút)
    let isOnline = false;
    if (botStatus === "online" && lastPing) {
        const now = Math.floor(Date.now() / 1000);
        if (now - lastPing < 400) {
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
    
    // Xử lý hiệu ứng chữ chạy cho tên quá dài
    document.querySelectorAll('.user-name-scroller').forEach(el => {
        if (el.scrollWidth > el.clientWidth) {
            const text = el.innerText;
            el.innerHTML = `<marquee scrollamount="3" behavior="scroll" direction="left" style="vertical-align: middle;">${text}</marquee>`;
            el.style.textOverflow = 'clip';
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
