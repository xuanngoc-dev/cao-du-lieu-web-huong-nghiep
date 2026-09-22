/* Shared helpers for Bootstrap UI */
window.App = {
  STORAGE_KEY: 'huong_nghiep_school_codes',

  saveCodes(text) {
    try { localStorage.setItem(this.STORAGE_KEY, text || ''); } catch (e) {}
  },

  loadCodes() {
    try { return localStorage.getItem(this.STORAGE_KEY) || ''; } catch (e) { return ''; }
  },

  parseCodes(text) {
    if (!text) return [];
    const raw = String(text).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    const tokens = [];
    raw.split('\n').forEach(line => {
      line = line.trim().replace(/^\d+[\.\)\-\:\t ]+\s*/, '');
      if (!line) return;
      const named = line.match(/^([A-Za-z0-9]{2,10})\s*[-–—:]\s+\S+/);
      if (named && !line.includes(',') && !line.includes(';')) {
        tokens.push(named[1]);
        return;
      }
      line.split(/[,;|\t]+/).forEach(part => {
        part.trim().split(/\s+/).forEach(p => {
          p = p.replace(/[()[\]{}]/g, '').trim();
          if (p) tokens.push(p);
        });
      });
    });
    const out = [];
    const seen = new Set();
    tokens.forEach(t => {
      if (!/^[A-Za-z0-9]{2,10}$/.test(t)) return;
      const c = t.toUpperCase();
      if (['STT', 'MA', 'CODE', 'TYPE', 'NAME', 'ALL'].includes(c)) return;
      if (seen.has(c)) return;
      seen.add(c);
      out.push(c);
    });
    return out;
  },

  async copyText(text) {
    try {
      await navigator.clipboard.writeText(text || '');
      return true;
    } catch (e) {
      const ta = document.createElement('textarea');
      ta.value = text || '';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      return true;
    }
  },

  toast(message) {
    let el = document.getElementById('appToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'appToast';
      el.className = 'toast align-items-center text-bg-dark border-0 position-fixed bottom-0 end-0 m-3';
      el.setAttribute('role', 'alert');
      el.innerHTML = `<div class="d-flex"><div class="toast-body"></div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
      document.body.appendChild(el);
    }
    el.querySelector('.toast-body').textContent = message;
    bootstrap.Toast.getOrCreateInstance(el, { delay: 2200 }).show();
  },

  /** Select2 helpers (cần jQuery + Select2 đã load) */
  select2Ready() {
    return typeof window.jQuery !== 'undefined' && typeof window.jQuery.fn.select2 === 'function';
  },

  destroySelect2(el) {
    if (!this.select2Ready() || !el) return;
    const $el = window.jQuery(el);
    if ($el.hasClass('select2-hidden-accessible')) {
      try { $el.select2('destroy'); } catch (e) {}
    }
  },

  initSelect2(el, opts) {
    if (!this.select2Ready() || !el) return;
    // Multi-select dùng widget CodingNepal, không Select2
    if (el.multiple && el.classList.contains('js-multi-select')) {
      this.initMultiSelect(el, opts);
      return;
    }
    const $el = window.jQuery(el);
    this.destroySelect2(el);
    const defaults = {
      theme: 'bootstrap-5',
      width: '100%',
      allowClear: !!el.multiple || el.querySelector('option[value=""]') != null,
      placeholder: el.getAttribute('data-placeholder') || (el.multiple ? 'Chọn…' : 'Chọn…'),
      language: {
        noResults: () => 'Không có kết quả',
        searching: () => 'Đang tìm…',
        removeAllItems: () => 'Xóa tất cả',
      },
    };
    $el.select2(Object.assign(defaults, opts || {}));
  },

  /**
   * Gán lại options cho <select> rồi (re)init Select2 / MultiSelect.
   * value: string | string[] | null — giữ / đặt giá trị sau khi đổ.
   */
  setSelectOptions(el, html, value, opts) {
    if (!el) return;
    const keep = value !== undefined
      ? value
      : (el.multiple
        ? [...el.selectedOptions].map(o => o.value)
        : el.value);
    // Destroy widgets trước khi đổi HTML
    if (el.multiple && (el.classList.contains('js-multi-select') || this.getMultiSelect?.(el))) {
      const ms = this.getMultiSelect?.(el);
      if (ms) ms.destroy();
    } else {
      this.destroySelect2(el);
    }
    el.innerHTML = html || '';
    if (el.multiple) {
      const set = new Set(Array.isArray(keep) ? keep : (keep ? [keep] : []));
      [...el.options].forEach(o => { o.selected = set.has(o.value); });
    } else if (keep != null && keep !== '' && [...el.options].some(o => o.value === String(keep))) {
      el.value = keep;
    }
    if (el.multiple && el.classList.contains('js-multi-select')) {
      this.initMultiSelect(el, opts);
    } else {
      this.initSelect2(el, opts);
    }
  },

  getSelectValues(el) {
    if (!el) return [];
    if (el.multiple) return [...el.selectedOptions].map(o => o.value).filter(Boolean);
    return el.value ? [el.value] : [];
  },
};
