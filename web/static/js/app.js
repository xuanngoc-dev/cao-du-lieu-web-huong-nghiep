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
  }
};
