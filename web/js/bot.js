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
    let statusClass = build.status === 'success' ? 'badge-susfs' 
        : build.status === 'building' ? 'badge-new' 
        : 'badge-deprecated';
        
    let statusLabel = build.status === 'success' ? 'Thành công'
        : build.status === 'building' ? 'Đang Build'
        : 'Lỗi';

    // Nút action (Download & Github)
    let actionButtons = '';
    if (build.status === 'success') {
        actionButtons = `
            <a href="${build.nightly_link}" target="_blank" class="bot-btn btn-success"><i class="fa-solid fa-download"></i> Tải về</a>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else if (build.status === 'building') {
        actionButtons = `
            <button class="bot-btn" disabled><i class="fa-solid fa-spinner fa-spin"></i> Đang biên dịch</button>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else {
        actionButtons = `
            <button class="bot-btn" disabled style="color: #ef4444; border-color: rgba(239, 68, 68, 0.3);"><i class="fa-solid fa-xmark"></i> Thất bại</button>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    }

    return `
        <div class="card" data-status="${build.status}">
            <div class="card-header">
                <span class="badge badge-android">${build.title || 'Unknown OS'}</span>
                <span class="badge badge-kernel">${build.sub_title || 'N/A'}</span>
                <span class="badge ${statusClass}">${statusLabel}</span>
            </div>
            
            <div class="stats" style="grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 0;">
                <div class="stat"><div class="stat-label">Thời gian</div><div class="stat-value" style="font-size: 0.9rem;">${formatDate(build.date)}</div></div>
                <div class="stat"><div class="stat-label">Người gửi</div><div class="stat-value user-name-scroller" style="font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${build.user_name || 'Unknown'}</div></div>
                <div class="stat" style="grid-column: 1 / -1;"><div class="stat-label">Custom Version</div><div class="stat-value" style="font-size: 0.9rem;">${build.custom_version || '(Mặc định)'}</div></div>
                <div class="stat"><div class="stat-label">Thông số</div><div class="stat-value" style="font-size: 0.85rem;">ZRAM: ${build.zram} | KPM: ${build.kpm}</div></div>
                <div class="stat"><div class="stat-label">Mô-đun</div><div class="stat-value" style="font-size: 0.85rem;">BBG: ${build.bbg} | SUSFS: ${build.susfs}</div></div>
            </div>

            <div class="card-actions" style="padding: 15px 20px; display: flex; gap: 10px; border-top: 1px solid var(--border); margin-top: 15px;">
                ${actionButtons}
            </div>
        </div>
    `;
}

// Cập nhật trạng thái Bot (Online/Offline)
function updateStatusBadge(botStatus, lastPing) {
    const badge = document.getElementById('botStatusBadge');
    if (!badge) return;
    const textSpan = document.getElementById('botStatusText');

    // Nếu quá 6.5 phút (400s) không ping, coi như offline
    let isOnline = false;
    if (botStatus === "online" && lastPing) {
        const now = Math.floor(Date.now() / 1000);
        if (now - lastPing < 400) {
            isOnline = true;
        }
    }

    if (isOnline) {
        badge.className = "status-btn online";
        if (textSpan) textSpan.innerText = "Online";
    } else {
        badge.className = "status-btn offline";
        if (textSpan) textSpan.innerText = "Offline";
    }
}

// Xử lý Render
function renderBuilds(filter = 'all') {
    const container = document.getElementById('builds-container');
    if (!container) return;
    container.innerHTML = '';

    if (currentBuilds.length === 0) {
        container.innerHTML = '<p style="color: var(--text-muted); grid-column: 1 / -1; text-align: center; padding: 40px;">Đang tải dữ liệu hoặc chưa có bản build nào...</p>';
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
        const activeFilterBtn = document.querySelector('.filter-btn.active');
        const activeFilter = activeFilterBtn ? activeFilterBtn.dataset.filter : 'all';
        renderBuilds(activeFilter);
    } catch (err) {
        console.error("Lỗi lấy dữ liệu:", err);
        updateStatusBadge("offline", 0);
    }
}

export function initBot() {
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
}
