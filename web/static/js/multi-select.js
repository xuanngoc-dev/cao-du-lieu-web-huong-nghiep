/**
 * Multi-select dropdown kiểu CodingNepal
 * (checkbox list + nút mũi tên tròn).
 * Đồng bộ với <select multiple> ẩn.
 */
(function (global) {
  const INSTANCES = new WeakMap();

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  class MultiSelect {
    constructor(selectEl, opts) {
      this.select = selectEl;
      this.opts = Object.assign({
        placeholder: selectEl.getAttribute('data-placeholder') || 'Chọn…',
        search: true,
        selectAll: true,
        maxHeight: 280,
      }, opts || {});
      this._onDocClick = this._onDocClick.bind(this);
      this._build();
      this._bind();
      this.refreshFromSelect();
      INSTANCES.set(selectEl, this);
    }

    _build() {
      this.select.classList.add('cn-multi-native');
      this.select.setAttribute('aria-hidden', 'true');
      this.select.tabIndex = -1;

      const wrap = document.createElement('div');
      wrap.className = 'cn-multi';
      if (this.select.disabled) wrap.classList.add('is-disabled');

      wrap.innerHTML = `
        <div class="cn-multi-btn" role="button" tabindex="0" aria-haspopup="listbox" aria-expanded="false">
          <span class="cn-multi-text">${esc(this.opts.placeholder)}</span>
          <span class="cn-multi-arrow"><i class="bi bi-chevron-down"></i></span>
        </div>
        <div class="cn-multi-panel" hidden>
          ${this.opts.search ? `
            <div class="cn-multi-search">
              <i class="bi bi-search"></i>
              <input type="text" class="cn-multi-search-input" placeholder="Tìm…" autocomplete="off">
            </div>` : ''}
          ${this.opts.selectAll ? `
            <div class="cn-multi-toolbar">
              <button type="button" class="cn-multi-link" data-action="all">Chọn hết</button>
              <button type="button" class="cn-multi-link" data-action="none">Bỏ chọn</button>
            </div>` : ''}
          <ul class="cn-multi-list" role="listbox" aria-multiselectable="true"></ul>
          <div class="cn-multi-empty" hidden>Không có kết quả</div>
        </div>
      `;

      this.select.insertAdjacentElement('afterend', wrap);
      this.wrap = wrap;
      this.btn = wrap.querySelector('.cn-multi-btn');
      this.textEl = wrap.querySelector('.cn-multi-text');
      this.panel = wrap.querySelector('.cn-multi-panel');
      this.list = wrap.querySelector('.cn-multi-list');
      this.emptyEl = wrap.querySelector('.cn-multi-empty');
      this.searchInput = wrap.querySelector('.cn-multi-search-input');
    }

    _bind() {
      this.btn.addEventListener('click', (e) => {
        e.preventDefault();
        if (this.select.disabled) return;
        this.toggle();
      });
      this.btn.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          this.toggle();
        } else if (e.key === 'Escape') {
          this.close();
        }
      });

      this.list.addEventListener('click', (e) => {
        const item = e.target.closest('.cn-multi-item');
        if (!item) return;
        e.preventDefault();
        this._toggleItem(item.dataset.value);
      });

      this.wrap.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (btn.dataset.action === 'all') this.selectAllVisible();
          if (btn.dataset.action === 'none') this.clear();
        });
      });

      if (this.searchInput) {
        this.searchInput.addEventListener('input', () => this._filter(this.searchInput.value));
        this.searchInput.addEventListener('click', (e) => e.stopPropagation());
        this.searchInput.addEventListener('keydown', (e) => {
          if (e.key === 'Escape') this.close();
        });
      }

      document.addEventListener('click', this._onDocClick);
    }

    _onDocClick(e) {
      if (!this.wrap.contains(e.target) && e.target !== this.select) {
        this.close();
      }
    }

    open() {
      if (this.select.disabled) return;
      this.btn.classList.add('open');
      this.btn.setAttribute('aria-expanded', 'true');
      this.panel.hidden = false;
      if (this.searchInput) {
        this.searchInput.value = '';
        this._filter('');
        setTimeout(() => this.searchInput.focus(), 10);
      }
    }

    close() {
      this.btn.classList.remove('open');
      this.btn.setAttribute('aria-expanded', 'false');
      this.panel.hidden = true;
    }

    toggle() {
      if (this.btn.classList.contains('open')) this.close();
      else this.open();
    }

    refreshFromSelect() {
      this.wrap.classList.toggle('is-disabled', !!this.select.disabled);
      const opts = [...this.select.options].filter(o => o.value !== '');
      this.list.innerHTML = opts.map(o => `
        <li class="cn-multi-item ${o.selected ? 'checked' : ''}"
            data-value="${esc(o.value)}"
            data-label="${esc(o.textContent)}"
            role="option"
            aria-selected="${o.selected ? 'true' : 'false'}">
          <span class="cn-multi-check"><i class="bi bi-check2 check-icon"></i></span>
          <span class="cn-multi-item-text">${esc(o.textContent)}</span>
        </li>
      `).join('');
      this._updateBtnText();
      this._filter(this.searchInput ? this.searchInput.value : '');
    }

    _toggleItem(value) {
      const opt = [...this.select.options].find(o => o.value === value);
      if (!opt) return;
      opt.selected = !opt.selected;
      const item = this.list.querySelector(`.cn-multi-item[data-value="${CSS.escape(value)}"]`);
      if (item) {
        item.classList.toggle('checked', opt.selected);
        item.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
      }
      this._updateBtnText();
      this._notifyChange();
    }

    selectAllVisible() {
      this.list.querySelectorAll('.cn-multi-item:not([hidden])').forEach(item => {
        const opt = [...this.select.options].find(o => o.value === item.dataset.value);
        if (opt && !opt.selected) {
          opt.selected = true;
          item.classList.add('checked');
          item.setAttribute('aria-selected', 'true');
        }
      });
      this._updateBtnText();
      this._notifyChange();
    }

    clear() {
      [...this.select.options].forEach(o => { o.selected = false; });
      this.list.querySelectorAll('.cn-multi-item').forEach(item => {
        item.classList.remove('checked');
        item.setAttribute('aria-selected', 'false');
      });
      this._updateBtnText();
      this._notifyChange();
    }

    setValues(values, notify) {
      const set = new Set(Array.isArray(values) ? values.map(String) : []);
      [...this.select.options].forEach(o => { o.selected = set.has(o.value); });
      this.refreshFromSelect();
      if (notify) this._notifyChange();
    }

    _notifyChange() {
      // Một lần thôi — jQuery .on('change') cũng nhận native Event
      this.select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    getValues() {
      return [...this.select.selectedOptions].map(o => o.value).filter(Boolean);
    }

    _updateBtnText() {
      const n = this.getValues().length;
      const total = [...this.select.options].filter(o => o.value).length;
      if (!n) {
        this.textEl.textContent = this.opts.placeholder;
      } else if (n === total && total > 1) {
        this.textEl.textContent = `Đã chọn hết (${n})`;
      } else if (n === 1) {
        const opt = this.select.selectedOptions[0];
        this.textEl.textContent = opt ? opt.textContent : `${n} đã chọn`;
      } else {
        this.textEl.textContent = `${n} đã chọn`;
      }
    }

    _filter(q) {
      const query = String(q || '').trim().toLowerCase();
      let visible = 0;
      this.list.querySelectorAll('.cn-multi-item').forEach(item => {
        const label = (item.dataset.label || '').toLowerCase();
        const show = !query || label.includes(query);
        item.hidden = !show;
        if (show) visible++;
      });
      if (this.emptyEl) this.emptyEl.hidden = visible > 0;
    }

    destroy() {
      document.removeEventListener('click', this._onDocClick);
      if (this.wrap && this.wrap.parentNode) this.wrap.remove();
      this.select.classList.remove('cn-multi-native');
      this.select.removeAttribute('aria-hidden');
      this.select.tabIndex = 0;
      INSTANCES.delete(this.select);
    }
  }

  function getInstance(el) {
    return el ? INSTANCES.get(el) : null;
  }

  function initMultiSelect(el, opts) {
    if (!el) return null;
    const existing = getInstance(el);
    if (existing) {
      existing.destroy();
    }
    // Không dùng Select2 trên multi này
    if (global.App && global.App.destroySelect2) global.App.destroySelect2(el);
    return new MultiSelect(el, opts);
  }

  global.App = global.App || {};
  global.App.MultiSelect = MultiSelect;
  global.App.initMultiSelect = initMultiSelect;
  global.App.getMultiSelect = getInstance;
})(window);
