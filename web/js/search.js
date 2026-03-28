/**
 * 搜索过滤模块
 */

import { t } from './i18n.js';

export function initSearch() {
  var searchToggle = document.getElementById('searchToggle');
  var searchInput = document.getElementById('searchInput');
  searchInput.placeholder = t.searchPlaceholder;

  // 搜索框展开/收起
  searchToggle.addEventListener('click', function () {
    var isOpen = searchInput.classList.toggle('open');
    if (isOpen) {
      searchInput.style.display = '';
      searchInput.focus();
    } else {
      searchInput.value = '';
      searchInput.style.display = 'none';
      filterActivePanel('');
    }
  });

  // 输入时实时过滤
  searchInput.addEventListener('input', function () {
    filterActivePanel(searchInput.value);
  });
}

// 过滤当前激活面板中的表格行
function filterActivePanel(rawQuery) {
  var panel = document.querySelector('.tab-panel.active');
  if (!panel) return;

  var query = rawQuery.trim().toLowerCase();
  var cards = panel.querySelectorAll('.card');

  cards.forEach(function (card) {
    if (!query) {
      card.style.display = '';
      var rows = card.querySelectorAll('.table-dual-wrapper tbody tr');
      rows.forEach(function(row) { row.style.display = ''; });
      return;
    }
    
    var wrapper = card.querySelector('.table-dual-wrapper');
    if (wrapper) {
      // Tích hợp tìm kiếm nội dung tổng thể (Ví dụ: config ZRAM, BBG hoăc Tiêu đề thẻ Web Build)
      var titleText = (card.querySelector('h3') ? card.querySelector('h3').textContent : '').toLowerCase();
      var configText = (card.textContent || '').toLowerCase(); // Bao gồm cả thông tin người chạy "CitrusChan"
      
      var rows = wrapper.querySelectorAll('tbody tr');
      var visibleCount = 0;
      
      // Nếu có bất kì từ nào khớp vào config/tiêu đề, có thể hiện toàn bộ
      rows.forEach(function(row) {
        var rowText = (row.textContent || '').toLowerCase();
        var match = rowText.indexOf(query) !== -1 || configText.indexOf(query) !== -1;
        row.style.display = match ? '' : 'none';
        if (match) visibleCount++;
      });
      
      // Ẩn toàn bộ Card nếu không có row nào thoả mãn (Và card cũng không chứa từ khóa)
      if (visibleCount === 0) {
        card.style.display = 'none';
      } else {
        card.style.display = '';
      }
    } else {
      // Xử lý đối với Bot Build (Single Card không có Table)
      var text = (card.textContent || '').toLowerCase();
      var match = text.indexOf(query) !== -1;
      card.style.display = match ? '' : 'none';
    }
  });
}
