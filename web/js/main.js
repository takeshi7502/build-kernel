/**
 * 入口模块：初始化所有子模块，加载并渲染内核数据
 */

// 导入 SCSS 样式（webpack 会处理打包）
import '../scss/main.scss';

import { t } from './i18n.js';
import { initI18n } from './i18n.js';
import { initTheme } from './theme.js';
import { initModal, hideModal } from './modal.js';
import { initAnnouncement, hideAnnounce } from './announcement.js';
import { initSearch } from './search.js';
import { initTimeConverter } from './time-converter.js';
import { initBackToTop } from './back-to-top.js';
import { showToast } from './toast.js';
import { copyText, fetchJsonFresh } from './utils.js';
import { DATA_FILES } from './config.js';
import { renderTabs, renderPanels } from './render.js';
import { initBot } from './bot.js';

// 初始化各模块
initI18n();
initTheme();
initModal();
initSearch();
initTimeConverter();
initBackToTop();
initBot();

// ESC 关闭所有弹窗
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') { hideModal(); hideAnnounce(); }
});

// 复制按钮全局事件委托（弹窗 + 时间转换器共用）
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.modal-copy');
  if (!btn) return;
  var text = btn.dataset.copy;
  if (!text) return;
  copyText(text).then(function () {
    btn.textContent = t.copied;
    btn.classList.add('copied');
    showToast(t.tcToast);
    setTimeout(function () {
      btn.textContent = t.copy;
      btn.classList.remove('copied');
    }, 1500);
  }).catch(function () {});
});

let lastDataStr = "";

// 加载内核数据
async function loadData(isInterval = false) {
  var results = await Promise.allSettled(
    DATA_FILES.map(function (f) {
      return fetchJsonFresh('data/' + f.android + '/' + f.kernel + '.json');
    })
  );

  var datasets = [];
  for (var i = 0; i < results.length; i++) {
    if (results[i].status === 'fulfilled') {
      datasets.push({ meta: DATA_FILES[i], data: results[i].value });
    }
  }

  const newStr = JSON.stringify(datasets);
  if (isInterval && lastDataStr === newStr) {
      return; // Không có thanh đổi JSON, bỏ qua render lại
  }
  
  const isFirstLoad = !lastDataStr;
  lastDataStr = newStr;

  if (datasets.length === 0) {
    var err = document.createElement('div');
    err.className = 'error';
    err.innerHTML = '<p>' + t.errorTitle + '</p><p style="margin-top:0.5rem;color:var(--text-muted)">' + t.errorHint + '</p>';
    document.getElementById('content').appendChild(err);
    var mainLoading = document.getElementById('mainLoading');
    if (mainLoading) mainLoading.style.display = 'none';
    return;
  }

  var activePanelId = document.querySelector('.tab-panel.active')?.id;

  if (isFirstLoad) {
      renderTabs(datasets);
      // Attach event to the static tabs (Bot Build and Web Build) so users can switch back to them
      ['tab-bot', 'tab-web'].forEach(function(tabId) {
        var tab = document.getElementById(tabId);
        if (tab) {
            tab.addEventListener('click', function () {
                document.querySelectorAll('.tab').forEach(function (t) { t.classList.remove('active'); });
                document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
                tab.classList.add('active');
                var panel = document.getElementById(tab.dataset.panel);
                if (panel) panel.classList.add('active');
            });
        }
      });
  }

  renderPanels(datasets);

  if (!isFirstLoad && activePanelId) {
      // Phục hồi panel nào đang mở
      var currPanel = document.getElementById(activePanelId);
      if (currPanel) {
          currPanel.classList.add('active');
      }
  }
}

// 启动
initAnnouncement();
loadData();

// Tự động kiểm tra file JSON mỗi 20 giây để update realtime
setInterval(function() {
    loadData(true);
}, 20000);

