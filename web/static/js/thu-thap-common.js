var schoolRows = [];
var collectBusy = false;
var methodRows = [];
var methodDocuments = [];
var methodGroups = {};
var resultRows = [];
var OFFICIAL_METHODS = window.App.OFFICIAL_METHODS;

document.querySelectorAll('select.js-multi-select').forEach(el => {
  window.App.initMultiSelect(el, {
    placeholder: el.getAttribute('data-placeholder') || 'Chọn…',
  });
});

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function checkedValues(id) {
  return window.App.getSelectValues(document.getElementById(id));
}

function filteredSchools() {
  const types = checkedValues('filterType');
  const sectors = checkedValues('filterSector');
  const regions = checkedValues('filterRegion');
  return schoolRows.filter(s => {
    if (types.length && !types.includes(s.type_label || '')) return false;
    if (sectors.length && !sectors.includes(s.loai_truong || '')) return false;
    if (regions.length) {
      const area = String(s.khu_vuc || '');
      if (!regions.some(region => area.includes(region))) return false;
    }
    return true;
  });
}

function renderSchoolPick() {
  const rows = filteredSchools();
  const el = document.getElementById('schoolPick');
  const prev = window.App.getSelectValues(el);
  const html = rows.map(s => {
    const code = String(s.code || '').toUpperCase();
    return `<option value="${escapeHtml(code)}">${escapeHtml(code)} - ${escapeHtml(s.name || '')}</option>`;
  }).join('');
  window.App.setSelectOptions(el, html, prev, { placeholder: 'Chọn trường' });
}

function sourceUrls(texts) {
  const found = String((texts || []).join(' ')).match(/https?:\/\/[^\s<>"')]+/g) || [];
  return [...new Set(found.map(url => url.replace(/[.,;]+$/, '')))];
}

function codeName(code, name) {
  const label = String(code || '').trim();
  const title = String(name || '').trim();
  if (label && title) return `[${label}] - ${title}`;
  return label || title || '—';
}

function formatCollectedAt(value) {
  const text = String(value || '').trim();
  if (!text) return '—';
  const parsed = text.replace('T', ' ').replace(/\.\d+$/, '');
  return parsed.length >= 16 ? parsed.slice(0, 16) : parsed;
}

function resultOptionHtml(pairs) {
  return pairs
    .sort((a, b) => a[1].localeCompare(b[1], 'vi'))
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join('');
}

function setProgress(mode, label, pct) {
  const wrap = document.getElementById('collectProgress');
  const bar = document.getElementById('collectProgressBar');
  const track = document.getElementById('collectProgressTrack');
  if (mode === 'hide') {
    wrap.classList.add('d-none');
    return;
  }
  wrap.classList.remove('d-none');
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  document.getElementById('collectProgressLabel').textContent = label || '';
  document.getElementById('collectProgressPct').textContent = `${p}%`;
  bar.style.width = `${p}%`;
  bar.textContent = `${p}%`;
  track.setAttribute('aria-valuenow', String(p));
  const done = mode === 'done';
  bar.classList.toggle('progress-bar-animated', !done);
  bar.classList.toggle('progress-bar-striped', !done);
  bar.classList.toggle('bg-success', done);
  bar.classList.toggle('bg-teal', !done);
}

function selectedYears() {
  return window.App.getSelectValues(document.getElementById('crawlYears')).map(Number).filter(Boolean);
}

function renderSectorFilters() {
  const sectors = [...new Set(schoolRows.map(s => s.loai_truong || '').filter(Boolean))].sort((a, b) => a.localeCompare(b, 'vi'));
  const html = sectors.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
  window.App.setSelectOptions(document.getElementById('filterSector'), html, [], {
    placeholder: 'Tất cả loại trường',
  });
}

function setCollectBusy(on) {
  collectBusy = on;
  const button = document.getElementById('btnCollect');
  if (button) button.disabled = on;
}

function methodItems(row) {
  return Array.isArray(row.hinh_thuc) ? row.hinh_thuc : [];
}
function isTmuAllMethod(name) {
  const folded = String(name || '').toLowerCase();
  return folded.includes('tất cả') && folded.includes('phương thức');
}

function officialMethod(name) {
  return OFFICIAL_METHODS.find(item => item.ma === String(name || '').trim()) || null;
}

function readGroupCodes(school, year, name) {
  const schoolGroups = methodGroups[String(school || '').trim().toUpperCase()] || {};
  const yearGroups = schoolGroups[String(year || '')] || {};
  const codes = yearGroups[name];
  return Array.isArray(codes) ? codes.filter(code => officialMethod(code)) : [];
}

function methodNamesForSchool(school, year) {
  const schoolCode = String(school || '').trim().toUpperCase();
  const yearText = String(year || '');
  const names = [];
  methodRows.forEach(row => {
    if (String(row.ma_truong || '').trim().toUpperCase() !== schoolCode) return;
    if (yearText && String(row.nam || '') !== yearText) return;
    methodItems(row).forEach(item => {
      const name = String(item.ten || item.id || '').trim();
      if (name && !isTmuAllMethod(name) && !names.includes(name)) names.push(name);
    });
  });
  return names;
}

function aliasMethodName(school, year, name) {
  const label = String(name || '').trim();
  if (!label || officialMethod(label)) return '';
  const methods = methodNamesForSchool(school, year).filter(item => item !== label);
  const byParen = methods.filter(item => {
    const match = item.match(/\(([^)]+)\)\s*$/);
    const code = match ? match[1].trim() : '';
    return code && (label === code || label.startsWith(`${code}_`));
  });
  if (byParen.length === 1) return byParen[0];
  const token = new RegExp(`(?:^|\\s)${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:$|\\s)`, 'i');
  const byToken = methods.filter(item => token.test(item));
  return byToken.length === 1 ? byToken[0] : '';
}

function methodGroupCodes(school, year, name) {
  const direct = readGroupCodes(school, year, name);
  if (direct.length) return direct;
  const alias = aliasMethodName(school, year, name);
  return alias ? readGroupCodes(school, year, alias) : [];
}

function methodColumnNote(name, school, year) {
  const official = officialMethod(name);
  if (official) return {kind: 'official', title: `${official.ma}: ${official.ten}`, lines: [`${official.ma}: ${official.ten}`]};
  const codes = methodGroupCodes(school, year, name);
  if (!codes.length) {
    return {
      kind: 'empty',
      title: `${name}: chưa gắn tổ hợp phương thức chuẩn. Bấm để cập nhật.`,
      lines: ['Chưa gắn tổ hợp phương thức chuẩn'],
    };
  }
  const lines = codes.map(code => {
    const item = officialMethod(code);
    return `${item.ma}: ${item.ten}`;
  });
  return {kind: 'group', title: `${name} gồm ${lines.join('; ')}`, lines};
}

let methodGroupTarget = null;

function openMethodGroup(name, school, year) {
  methodGroupTarget = {name, school: String(school || '').toUpperCase(), year: String(year || '')};
  const selected = new Set(methodGroupCodes(school, year, name));
  document.getElementById('methodGroupCaption').textContent = `${school} · Năm ${year} · ${name}`;
  document.getElementById('methodGroupChoices').innerHTML = OFFICIAL_METHODS.map(item => `
    <label class="form-check">
      <input class="form-check-input" type="checkbox" value="${escapeHtml(item.ma)}" ${selected.has(item.ma) ? 'checked' : ''}>
      <span class="form-check-label"><b>${escapeHtml(item.ma)}</b> — ${escapeHtml(item.ten)}</span>
    </label>`).join('');
  const result = document.getElementById('methodGroupResult');
  result.className = 'alert d-none mt-3 mb-0';
  result.textContent = '';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('methodGroupModal')).show();
}

const methodGroupSave = document.getElementById('methodGroupSave');
if (methodGroupSave) methodGroupSave.addEventListener('click', async () => {
  if (!methodGroupTarget) return;
  const result = document.getElementById('methodGroupResult');
  const codes = [...document.querySelectorAll('#methodGroupChoices input:checked')].map(input => input.value);
  document.getElementById('methodGroupSave').disabled = true;
  try {
    const res = await fetch('/api/phuong-thuc/nhom', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        ma_truong: methodGroupTarget.school,
        nam: Number(methodGroupTarget.year),
        ten: methodGroupTarget.name,
        ma: codes,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Không lưu được tổ hợp');
    methodGroups = data.nhom_phuong_thuc || {};
    if (typeof renderMethods === "function") renderMethods(methodRows, methodDocuments);
    if (typeof renderResults === "function" && resultRows.length) renderResults();
    bootstrap.Modal.getInstance(document.getElementById('methodGroupModal'))?.hide();
  } catch (e) {
    result.className = 'alert alert-danger mt-3 mb-0';
    result.textContent = e.message;
  } finally {
    document.getElementById('methodGroupSave').disabled = false;
  }
});

function confirmDeleteResults(message) {
  const modalEl = document.getElementById('resultDeleteModal');
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  document.getElementById('resultDeleteMessage').textContent = message;
  return new Promise((resolve) => {
    const okBtn = document.getElementById('resultDeleteConfirm');
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      okBtn.removeEventListener('click', onOk);
      modalEl.removeEventListener('hidden.bs.modal', onHidden);
      resolve(ok);
    };
    const onOk = () => {
      finish(true);
      modal.hide();
    };
    const onHidden = () => finish(false);
    okBtn.addEventListener('click', onOk);
    modalEl.addEventListener('hidden.bs.modal', onHidden, { once: true });
    modal.show();
  });
}

function updatePickSelect(pickClass, allId, buttonId, selected) {
  const boxes = [...document.querySelectorAll(pickClass)];
  const checked = boxes.filter(box => box.checked).length;
  const all = document.getElementById(allId);
  if (all) {
    all.checked = boxes.length > 0 && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  }
  const button = document.getElementById(buttonId);
  if (button) button.disabled = selected.size === 0;
}

function updateBonusSelect() {
  updatePickSelect('.bonus-pick', 'bonusCheckAll', 'btnDeleteBonus', selectedBonusKeys);
}

function updateConvertSelect() {
  updatePickSelect('.convert-pick', 'convertCheckAll', 'btnDeleteConvert', selectedConvertKeys);
}

function bindPickTable(tableId, pickClass, allId, selected, update) {
  document.getElementById(tableId).addEventListener('change', (event) => {
    const all = event.target.id === allId ? event.target : null;
    if (all) {
      document.querySelectorAll(pickClass).forEach(box => {
        box.checked = all.checked;
        const key = box.dataset.key || '';
        if (all.checked) selected.add(key);
        else selected.delete(key);
      });
      update();
      return;
    }
    const box = event.target.closest(pickClass);
    if (!box) return;
    const key = box.dataset.key || '';
    if (box.checked) selected.add(key);
    else selected.delete(key);
    update();
  });
}


['filterType', 'filterSector', 'filterRegion'].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('change', renderSchoolPick);
});

async function loadCollectSchools() {
  try {
    const res = await fetch('/api/schools/saved');
    const data = await res.json();
    schoolRows = (data && data.ok && data.schools) || [];
    renderSectorFilters();
  } catch (_) {
    schoolRows = [];
  }
  renderSchoolPick();
}
