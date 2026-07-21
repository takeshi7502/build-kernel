/**
 * 详情弹窗模块 — hiển thị nút tải xuống theo KernelSU Variant
 */

import { t } from './i18n.js';
import { esc } from './utils.js';

var modal = document.getElementById('modal');
var modalTitle = document.getElementById('modalTitle');
var modalBody = document.getElementById('modalBody');
var modalClose = document.getElementById('modalClose');

// Danh sách variant cố định theo thứ tự ưu tiên hiển thị
var VARIANT_ORDER = ['SukiSU', 'ReSukiSU', 'KittiSU', 'Next', 'MKSU', 'Official'];

// Hiển thị popup với danh sách nút tải xuống theo variant
export function showModal(android, kernel, sublevel, patch, downloads) {
  var fullVer = sublevel && sublevel !== 'lts'
    ? (kernel + '.' + sublevel)
    : kernel;

  modalTitle.textContent = fullVer;

  var rows = '';

  VARIANT_ORDER.forEach(function (variant) {
    var item = downloads && downloads[variant] ? downloads[variant] : null;
    var link = typeof item === 'string' ? item : (item && item.url ? item.url : null);
    var configLabel = item && typeof item === 'object' && item.config ? String(item.config) : '';
    var variantLabel = variant + (configLabel ? ' — ' + configLabel : '');

    var btn = link
      ? '<a class="modal-dl-btn modal-dl-btn--active" href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">📥 Tải xuống</a>'
      : '<span class="modal-dl-btn modal-dl-btn--disabled" aria-disabled="true">Chưa có</span>';

    rows +=
      '<div class="modal-row modal-variant-row">' +
        '<span class="modal-label modal-variant-label">' + esc(variantLabel) + '</span>' +
        btn +
      '</div>';
  });

  modalBody.innerHTML =
    '<div class="modal-variant-header">' +
      '<span>' + esc(android) + '</span>' +
      '<span class="badge badge-kernel">Kernel ' + esc(kernel) + '</span>' +
    '</div>' +
    rows;

  modal.style.display = '';
}

export function hideModal() {
  modal.style.display = 'none';
}

export function initModal() {
  modalClose.addEventListener('click', hideModal);
  modal.addEventListener('click', function (e) {
    if (e.target === modal) hideModal();
  });

  document.addEventListener('click', function (e) {
    // LTS box
    var ltsBox = e.target.closest('.lts-clickable');
    if (ltsBox) {
      var dl = {};
      try { dl = JSON.parse(ltsBox.dataset.downloads || '{}'); } catch (_) {}
      showModal(
        ltsBox.dataset.android,
        ltsBox.dataset.kernel,
        ltsBox.dataset.sublevel,
        ltsBox.dataset.patch,
        dl
      );
      return;
    }
    // Table row
    var row = e.target.closest('.row-clickable');
    if (!row) return;
    var dl = {};
    try { dl = JSON.parse(row.dataset.downloads || '{}'); } catch (_) {}
    showModal(row.dataset.android, row.dataset.kernel, row.dataset.sublevel, row.dataset.patch, dl);
  });
}
