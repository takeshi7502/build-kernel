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
    let statusStyle = build.status === 'success' ? 'background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.3);' 
        : build.status === 'building' ? 'background:rgba(234,179,8,0.15);color:#eab308;border:1px solid rgba(234,179,8,0.3);' 
        : 'background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.3);';
        
    let statusLabel = build.status === 'success' ? 'Success'
        : build.status === 'building' ? 'Building'
        : 'Failed';

    // Nút action (Download & Github)
    let actionButtons = '';
    if (build.status === 'success') {
        actionButtons = `
            <a href="${build.nightly_link}" target="_blank" class="bot-btn btn-success"><i class="fa-solid fa-download"></i> Download</a>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else if (build.status === 'building') {
        actionButtons = `
            <button class="bot-btn" disabled><i class="fa-solid fa-spinner fa-spin"></i> Compiling</button>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else {
        actionButtons = `
            <button class="bot-btn" disabled style="color: #ef4444; border-color: rgba(239, 68, 68, 0.3);"><i class="fa-solid fa-xmark"></i> Failed</button>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    }

    return `
        <div class="card" data-status="${build.status}" style="display: flex; flex-direction: column;">
            <div class="card-header" style="margin-bottom: 12px; display: flex; justify-content: space-between; align-items: flex-start;">
                <div style="flex: 1; min-width: 0; padding-right: 15px;">
                    <h3 style="margin: 0 0 4px 0; font-size: 1.1rem; font-weight: 600; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${build.title || 'Unknown OS'}</h3>
                    <div style="font-size: 0.85rem; font-weight: 500; color: var(--text-muted); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${build.sub_title || ''}</div>
                </div>
                <div class="badge" style="flex-shrink: 0; ${statusStyle}">${statusLabel}</div>
            </div>
            
            <div style="color: var(--text-muted); font-size: 0.85rem; display: flex; align-items: center; margin-bottom: 16px;">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 6px;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                <span style="white-space: nowrap;">${formatDate(build.date)}</span>
                <span style="margin: 0 6px;">by</span>
                <span style="font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px;">${build.user_name || 'Unknown'}</span>
            </div>
            
            <div style="background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; margin-bottom: 20px; font-family: 'Roboto Mono', monospace;">
                <div style="display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 12px; align-items: center;">
                    <span style="color: var(--text-muted); font-weight: 500;">Custom version:</span>
                    <span style="font-weight: 600; color: var(--text-primary); text-align: right; max-width: 60%; word-break: break-all;">${build.custom_version || '(Mặc định)'}</span>
                </div>
                
                <div style="display: flex; gap: 15px; font-size: 0.85rem; margin-bottom: 12px;">
                    <div style="flex: 1; display: flex; justify-content: space-between; align-items: center;">
                        <span style="color: var(--text-muted); font-weight: 500;">ZRAM:</span>
                        <span style="font-weight: 600; color: var(--text-primary);">${build.zram}</span>
                    </div>
                    <div style="flex: 1; display: flex; justify-content: space-between; align-items: center;">
                        <span style="color: var(--text-muted); font-weight: 500;">KPM:</span>
                        <span style="font-weight: 600; color: var(--text-primary);">${build.kpm}</span>
                    </div>
                </div>
                
                <div style="display: flex; gap: 15px; font-size: 0.85rem;">
                    <div style="flex: 1; display: flex; justify-content: space-between; align-items: center;">
                        <span style="color: var(--text-muted); font-weight: 500;">BBG:</span>
                        <span style="font-weight: 600; color: var(--text-primary);">${build.bbg}</span>
                    </div>
                    <div style="flex: 1; display: flex; justify-content: space-between; align-items: center;">
                        <span style="color: var(--text-muted); font-weight: 500;">SUSFS:</span>
                        <span style="font-weight: 600; color: var(--text-primary);">${build.susfs}</span>
                    </div>
                </div>
            </div>

            <div style="display: flex; gap: 10px; margin-top: auto; border-top: 1px solid var(--border); padding-top: 16px;">
                ${actionButtons}
            </div>
        </div>
    `;
}

// Card riêng cho Web Build (batch buildsave) — hiển thị bảng Version/Status
function createWebBuildCard(build) {
    let badge = '';
    if (build.status === 'success') {
        badge = '<span class="badge" style="background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.3);">Success</span>';
    } else if (build.status === 'building') {
        badge = '<span class="badge" style="background:rgba(234,179,8,0.15);color:#eab308;border:1px solid rgba(234,179,8,0.3);">Building</span>';
    } else if (build.status === 'partial') {
        badge = '<span class="badge" style="background:rgba(59,130,246,0.15);color:#3b82f6;border:1px solid rgba(59,130,246,0.3);">Partial ✅</span>';
    } else if (build.status === 'cancelled') {
        badge = '<span class="badge" style="background:rgba(245,158,11,0.15);color:#f59e0b;border:1px solid rgba(245,158,11,0.3);">Cancelled</span>';
    } else {
        badge = '<span class="badge" style="background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.3);">Failed</span>';
    }

    const subItems = build.sub_items || [];
    const rows = subItems.map(item => {
        let stClass = '', stLabel = '';
        if (item.status === 'success') { stClass = 'color:#10b981'; stLabel = '✅ Success'; }
        else if (item.status === 'building') { stClass = 'color:#eab308'; stLabel = '🔄 Building...'; }
        else if (item.status === 'cancelled') { stClass = 'color:#f59e0b'; stLabel = '🚫 Cancelled'; }
        else if (item.status === 'failed') { stClass = 'color:#ef4444'; stLabel = '❌ Failed'; }
        else { stClass = 'color:var(--text-muted)'; stLabel = '⏳ Waiting'; }
        let durStr = item.duration || '-';
        return `<tr>
            <td style="padding:5px 8px;font-size:0.82rem;color:var(--text-primary);font-family:'Roboto Mono',monospace;">${item.ver}</td>
            <td style="padding:5px 8px;font-size:0.82rem;${stClass};font-weight:600;text-align:center;white-space:nowrap;">${stLabel}</td>
            <td style="padding:5px 8px;font-size:0.75rem;color:var(--text-muted);text-align:right;">${durStr}</td>
        </tr>`;
    }).join('');

    let actionButtons = '';
    if (build.status === 'building') {
        actionButtons = `
            <button class="bot-btn" disabled><i class="fa-solid fa-spinner fa-spin"></i> Compiling</button>
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    } else {
        actionButtons = `
            <a href="${build.github_link}" target="_blank" class="bot-btn"><i class="fa-brands fa-github"></i> Github</a>
        `;
    }

    return `
        <div class="card web-build-card" data-status="${build.status}" style="break-inside:avoid;display:flex;flex-direction:column;">
            <div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:flex-start;">
                <div style="flex:1;min-width:0;padding-right:12px;">
                    <h3 style="margin:0 0 4px 0;font-size:1.1rem;font-weight:600;color:var(--text-primary);">${build.title || 'Unknown'}</h3>
                    <div style="font-size:0.83rem;font-weight:500;color:var(--text-muted);">${build.sub_title || ''}</div>
                </div>
                <div style="flex-shrink:0;">${badge}</div>
            </div>

            <div style="color:var(--text-muted);font-size:0.82rem;display:flex;align-items:center;margin-bottom:14px;">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:5px;flex-shrink:0;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                <span style="white-space:nowrap;">${formatDate(build.date)}</span>
                <span style="margin:0 5px;">by</span>
                <span style="font-weight:600;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:130px;">${build.user_name || 'Unknown'}</span>
            </div>

            ${subItems.length > 0 ? `
            <div style="background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius-md);margin-bottom:16px;overflow:hidden;">
                <table style="width:100%;border-collapse:collapse;">
                    <thead>
                        <tr style="border-bottom:1px solid var(--border);">
                            <th style="padding:7px 8px;font-size:0.78rem;color:var(--text-muted);font-weight:600;text-align:left;background:rgba(255,255,255,0.02);">Version</th>
                            <th style="padding:7px 8px;font-size:0.78rem;color:var(--text-muted);font-weight:600;text-align:center;background:rgba(255,255,255,0.02);">Time</th>
                            <th style="padding:7px 8px;font-size:0.78rem;color:var(--text-muted);font-weight:600;text-align:right;background:rgba(255,255,255,0.02);">Status</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>` : ''}

            <div style="display:flex;gap:10px;margin-top:auto;border-top:1px solid var(--border);padding-top:14px;">
                ${actionButtons}
            </div>
        </div>
    `;
}



let currentBotBuilds = [];
let currentWebBuilds = [];

// Cập nhật trạng thái Bot (Online/Offline)
function updateStatusBadge(botStatus, lastPing) {
    const badge = document.getElementById('botStatusBadge');
    if (!badge) return;
    const textSpan = document.getElementById('botStatusText');

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
function renderBuilds() {
    const filterBot = document.querySelector('.filter-group[data-target="bot"] .filter-btn.active')?.dataset.filter || 'all';
    const filterWeb = document.querySelector('.filter-group[data-target="web"] .filter-btn.active')?.dataset.filter || 'all';

    const containerBot = document.getElementById('builds-container');
    const containerWeb = document.getElementById('builds-container-web');

    if (containerBot) {
        containerBot.innerHTML = '';
        if (currentBotBuilds.length === 0) {
            containerBot.innerHTML = '<p style="color: var(--text-muted); grid-column: 1 / -1; text-align: center; padding: 40px;">Đang tải dữ liệu hoặc chưa có bản build nào...</p>';
        } else {
            currentBotBuilds.forEach(build => {
                if (filterBot === 'all' || build.status === filterBot) {
                    containerBot.innerHTML += createBuildCard(build);
                }
            });
        }
    }

    if (containerWeb) {
        containerWeb.innerHTML = '';
        if (currentWebBuilds.length === 0) {
            containerWeb.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">Đang tải dữ liệu hoặc chưa có bản web build nào...</p>';
        } else {
            currentWebBuilds.forEach(build => {
                if (filterWeb === 'all' || build.status === filterWeb) {
                    containerWeb.innerHTML += createWebBuildCard(build);
                }
            });
        }
    }
    
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

        const allBuilds = data.builds || [];
        currentBotBuilds = allBuilds.filter(b => b.type !== 'buildsave');
        currentWebBuilds = allBuilds.filter(b => b.type === 'buildsave');

        updateStatusBadge(data.status, data.last_ping);
        renderBuilds();
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
            const group = e.target.closest('.filter-group');
            if (group) {
                group.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            }
            e.target.classList.add('active');
            renderBuilds();
        });
    });

    // Auto Refresh mỗi 15 giây để luôn lấy dữ liệu mới nhất
    setInterval(loadData, 15000);
}
