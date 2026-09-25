var METHOD_ORDER = ['thpt', 'hoc_ba', 'dgnl', 'ket_hop', 'xttn_bo', 'xttn_truong'];
var METHOD_LABELS = {
  thpt: 'Xét tuyển bằng kết quả Kỳ thi tốt nghiệp THPT',
  hoc_ba: 'Xét tuyển bằng học bạ THPT',
  dgnl: 'Xét tuyển bằng kết quả Kỳ thi ĐGNL hoặc ĐGTD',
  ket_hop: 'Xét tuyển kết hợp / Đề án tuyển sinh riêng của trường',
  xttn_bo: 'Xét tuyển thẳng và ưu tiên xét tuyển theo quy chế của Bộ GD&ĐT',
  xttn_truong: 'Xét tuyển thẳng theo đề án riêng của từng trường',
};
var selectedMethodKeys = new Set();
function methodKey(row) {
  return [
    row.ma_truong || '',
    row.nam || '',
    row.ma_xet_tuyen || '',
    row.ma_nganh || '',
    row.ten_nganh || '',
  ].join('|');
}

function methodForms(row) {
  if (methodItems(row).length) {
    return methodItems(row)
      .filter(item => item.ap_dung !== false)
      .map(item => item.ten || item.id)
      .filter(Boolean);
  }
  const methods = row.phuong_thuc;
  if (methods && typeof methods === 'object' && !Array.isArray(methods)) {
    return METHOD_ORDER.filter(id => methods[id] && methods[id].co).map(id => METHOD_LABELS[id] || id);
  }
  return [];
}

function methodMajorLabel(row) {
  const code = String(row.ma_xet_tuyen || row.ma_nganh || '').trim();
  const name = String(row.ten_nganh || row.ten_chuong_trinh || '').trim();
  if (code && name) return `[${code}]-${name}`;
  return code || name || '—';
}

const TMU_METHOD_NOTES = [
  ['301', 'Xét tuyển thẳng, ưu tiên xét tuyển'],
  ['100', 'Thi THPT'],
  ['402', 'Xét tuyển theo kết quả HSA, TSA, SAT, ACT'],
  ['409', 'Xét tuyển kết hợp chứng chỉ ngoại ngữ còn hiệu lực tính đến ngày đăng ký xét tuyển với kết quả thi tốt nghiệp THPT'],
  ['410', 'Xét tuyển kết hợp chứng chỉ ngoại ngữ còn hiệu lực tính đến ngày đăng ký xét tuyển với kết quả học tập cấp THPT'],
  ['500', 'Xét tuyển kết hợp giải Nhất, Nhì, Ba trong kỳ thi chọn học sinh giỏi (cấp THPT) cấp tỉnh/thành phố trực'],
];

function methodComboList(item) {
  if (!item) return [];
  const detail = item.chi_tiet && typeof item.chi_tiet === 'object' ? item.chi_tiet : {};
  const raw = detail.to_hop_xet_tuyen || detail.to_hop || item.to_hop || [];
  if (Array.isArray(raw) && raw.length) {
    return raw.map(value => String(value).trim()).filter(Boolean);
  }
  const note = String(item.mo_ta || '').trim();
  if (note.toLowerCase().startsWith('tổ hợp:')) {
    return note.slice(note.indexOf(':') + 1).split(',').map(value => value.trim()).filter(Boolean);
  }
  return [];
}

function expandTmuMethods(row) {
  if (String(row.ma_truong || '').toUpperCase() !== 'TMU') return;
  const items = methodItems(row);
  const allItem = items.find(item => isTmuAllMethod(item.ten || item.id) && item.ap_dung !== false);
  if (!allItem) return;
  const shared = methodComboList(allItem);
  const kept = items.filter(item => !isTmuAllMethod(item.ten || item.id));
  const byId = new Map(kept.map(item => [String(item.id || item.ten), item]));
  TMU_METHOD_NOTES.forEach(([code, note]) => {
    const current = byId.get(code);
    if (current) {
      current.ap_dung = true;
      if (!methodComboList(current).length && shared.length) {
        current.chi_tiet = Object.assign({}, current.chi_tiet, {to_hop_xet_tuyen: shared});
        current.mo_ta = `Tổ hợp: ${shared.join(', ')}`;
      }
      return;
    }
    kept.push({
      id: code,
      ten: code,
      ap_dung: true,
      mo_ta: shared.length ? `Tổ hợp: ${shared.join(', ')}` : note,
      chi_tiet: shared.length ? {to_hop_xet_tuyen: shared.slice()} : {},
    });
  });
  row.hinh_thuc = kept;
}

function methodColumnNames(rows) {
  const names = [];
  (rows || []).forEach(row => {
    methodItems(row).forEach(item => {
      const name = item.ten || item.id;
      if (name && !isTmuAllMethod(name) && !names.includes(name)) names.push(name);
    });
  });
  const school = String((rows[0] || {}).ma_truong || '').toUpperCase();
  if (school !== 'TMU') return names;
  const preferred = TMU_METHOD_NOTES.map(([code]) => code);
  return [
    ...preferred.filter(code => names.includes(code)),
    ...names.filter(name => !preferred.includes(name)),
  ];
}

function methodHeaderLabel(name, school, year) {
  const note = methodColumnNote(name, school, year);
  return {label: name, title: note.title, kind: note.kind};
}

function methodApplies(row, name) {
  const hit = methodItems(row).find(item => (item.ten || item.id) === name);
  if (hit) return hit.ap_dung !== false;
  return methodForms(row).includes(name);
}

function methodCombos(row, name) {
  const forms = Array.isArray(row.hinh_thuc) ? row.hinh_thuc : [];
  return methodComboList(forms.find(item => (item.ten || item.id) === name));
}

let methodFilterSignature = '';

function methodMajorKey(row) {
  return String(row.ma_nganh || row.ten_nganh || '').trim();
}

function syncMethodFilters() {
  const schools = new Map();
  const years = new Set();
  const majors = new Map();
  methodRows.forEach(row => {
    const school = String(row.ma_truong || '').trim();
    if (school) schools.set(school, codeName(school, row.ten_truong));
    const year = String(row.nam || '').trim();
    if (year) years.add(year);
    const major = methodMajorKey(row);
    if (major) majors.set(major, codeName(row.ma_nganh, row.ten_nganh) || major);
  });
  const keep = (id) => window.App.getSelectValues(document.getElementById(id));
  window.App.setSelectOptions(document.getElementById('methodSchool'), resultOptionHtml([...schools]), keep('methodSchool'), {
    placeholder: 'Tất cả trường',
  });
  const yearHtml = [...years].sort((a, b) => Number(b) - Number(a))
    .map(year => `<option value="${escapeHtml(year)}">${escapeHtml(year)}</option>`)
    .join('');
  window.App.setSelectOptions(document.getElementById('methodYear'), yearHtml, keep('methodYear'), {
    placeholder: 'Tất cả năm',
  });
  window.App.setSelectOptions(document.getElementById('methodMajor'), resultOptionHtml([...majors]), keep('methodMajor'), {
    placeholder: 'Tất cả ngành',
  });
  const methodHtml = OFFICIAL_METHODS
    .map(item => `<option value="${escapeHtml(item.ma)}">${escapeHtml(item.ma)} — ${escapeHtml(item.ten)}</option>`)
    .join('');
  window.App.setSelectOptions(document.getElementById('methodForm'), methodHtml, keep('methodForm'), {
    placeholder: 'Tất cả phương thức',
  });
}

function rowOfficialCodes(row) {
  const codes = new Set();
  methodForms(row).forEach(name => {
    if (officialMethod(name)) codes.add(String(name).trim());
    methodGroupCodes(row.ma_truong, row.nam, name).forEach(code => codes.add(code));
  });
  return codes;
}

function methodRowVisible(row) {
  const schools = new Set(checkedValues('methodSchool'));
  const years = new Set(checkedValues('methodYear'));
  const majors = new Set(checkedValues('methodMajor'));
  const forms = new Set(checkedValues('methodForm'));
  if (schools.size && !schools.has(String(row.ma_truong || '').trim())) return false;
  if (years.size && !years.has(String(row.nam || '').trim())) return false;
  if (majors.size && !majors.has(methodMajorKey(row))) return false;
  if (forms.size && ![...rowOfficialCodes(row)].some(code => forms.has(code))) return false;
  const query = (document.getElementById('methodQuery')?.value || '').trim().toLowerCase();
  if (!query) return true;
  const blob = [row.ma_xet_tuyen, row.ten_chuong_trinh, row.ma_nganh, row.ten_nganh].join(' ').toLowerCase();
  return blob.includes(query);
}

function renderMethodDocuments() {
  const host = document.getElementById('methodDocs');
  if (!host) return;
  const schools = new Set(checkedValues('methodSchool'));
  const years = new Set(checkedValues('methodYear'));
  const docs = methodDocuments.filter(doc => {
    if (schools.size && !schools.has(String(doc.ma_truong || '').trim())) return false;
    if (years.size && !years.has(String(doc.nam || '').trim())) return false;
    return true;
  });
  if (!docs.length) {
    host.classList.add('d-none');
    host.innerHTML = '';
    return;
  }
  host.classList.remove('d-none');
  host.innerHTML = docs.map(doc => {
    const label = `${codeName(doc.ma_truong, doc.ten_truong)} · ${doc.nam || '—'} · ${doc.filename || 'tài liệu'}`;
    const link = doc.source_url
      ? `<a href="${escapeHtml(doc.source_url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
      : escapeHtml(label);
    return `<span class="me-3 d-inline-flex align-items-center gap-1">${link}
      <button type="button" class="btn btn-outline-danger btn-sm py-0 px-1" data-delete-method-doc="${escapeHtml(doc.stored_name || '')}" data-doc-school="${escapeHtml(doc.ma_truong || '')}" data-doc-year="${escapeHtml(doc.nam || '')}" title="Xoá tài liệu và dữ liệu đã nhập từ file này">
        <i class="bi bi-trash3"></i>
      </button></span>`;
  }).join('');
}

function renderMethods(rows, documents) {
  methodRows = rows || [];
  if (arguments.length > 1) methodDocuments = documents || [];
  const signature = methodRows.map(methodKey).join('\n');
  if (signature !== methodFilterSignature) {
    methodFilterSignature = signature;
    syncMethodFilters();
  }
  const alive = new Set(methodRows.map(methodKey));
  [...selectedMethodKeys].forEach(key => {
    if (!alive.has(key)) selectedMethodKeys.delete(key);
  });
  methodRows.forEach(expandTmuMethods);
  const visible = methodRows.map((row, i) => ({row, i})).filter(({row}) => methodRowVisible(row));
  const groups = new Map();
  visible.forEach(item => {
    const key = `${item.row.ma_truong || ''}|${item.row.nam || ''}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  const host = document.getElementById('methodTables');
  host.innerHTML = [...groups.values()].map(items => {
    const columns = methodColumnNames(items.map(item => item.row));
    const span = columns.length || 1;
    const sample = items[0].row;
    const body = items.map(({row, i}, index) => {
      const key = methodKey(row);
      const checked = selectedMethodKeys.has(key) ? 'checked' : '';
      const marks = (columns.length ? columns : [null]).map(name => {
        if (!name || !methodApplies(row, name)) return '<td class="text-center text-muted">—</td>';
        const combos = methodCombos(row, name);
        if (combos.length) {
          return `<td class="method-text small" title="${escapeHtml(name)}">${escapeHtml(combos.join(', '))}</td>`;
        }
        return `<td class="text-center" title="${escapeHtml(name)}"><i class="bi bi-check-circle-fill text-success"></i></td>`;
      }).join('');
      return `
      <tr>
        <td class="text-center"><input class="form-check-input method-pick" type="checkbox" data-key="${escapeHtml(key)}" aria-label="Chọn ngành ${index + 1}" ${checked}></td>
        <td class="text-muted text-center">${index + 1}</td>
        <td class="text-nowrap">${escapeHtml(codeName(row.ma_truong, row.ten_truong))}</td>
        <td class="text-center">${escapeHtml(row.nam || '—')}</td>
        <td class="text-nowrap">${escapeHtml(row.ma_xet_tuyen || '—')}</td>
        <td class="text-nowrap">${escapeHtml(row.ma_nganh || '—')}</td>
        <td class="method-text">${escapeHtml(row.ten_nganh || row.ten_chuong_trinh || '—')}</td>
        <td class="text-center">${escapeHtml(row.chi_tieu || '—')}</td>
        ${marks}
        <td class="method-text">${escapeHtml(row.ghi_chu || '—')}</td>
        <td class="text-nowrap">
          <button type="button" class="btn btn-outline-secondary btn-sm" data-method-detail="${i}">Chi tiết</button>
          <button type="button" class="btn btn-outline-danger btn-sm" data-delete-method="${escapeHtml(key)}" title="Xoá dòng này">
            <i class="bi bi-trash3"></i>
          </button>
        </td>
      </tr>`;
    }).join('');
    return `
    <table class="table table-sm table-bordered table-hover mb-0 align-middle table-nowrap-head method-data-table">
      <caption class="caption-top small text-muted px-2">${escapeHtml(codeName(sample.ma_truong, sample.ten_truong))} · Năm ${escapeHtml(sample.nam || '—')}</caption>
      <thead class="table-light">
        <tr>
          <th class="text-center" rowspan="2">
            <input class="form-check-input method-check-all" type="checkbox" aria-label="Chọn tất cả dòng của trường">
          </th>
          <th rowspan="2">STT</th>
          <th rowspan="2">Trường</th>
          <th rowspan="2">Năm học</th>
          <th rowspan="2">Mã xét tuyển</th>
          <th rowspan="2">Mã ngành</th>
          <th rowspan="2">Tên ngành</th>
          <th rowspan="2" class="text-center">Chỉ tiêu</th>
          <th class="text-center" colspan="${span}">Phương thức tuyển sinh</th>
          <th rowspan="2">Ghi chú</th>
          <th rowspan="2">Thao tác</th>
        </tr>
        <tr>
          ${(columns.length ? columns : ['Hình thức']).map(name => {
            const header = methodHeaderLabel(name, sample.ma_truong, sample.nam);
            if (header.kind === 'official' || name === 'Hình thức') {
              return `<th class="text-center" title="${escapeHtml(header.title)}">${escapeHtml(header.label)}</th>`;
            }
            return `<th class="text-center"><button type="button" class="btn btn-link btn-sm p-0 text-decoration-none" data-method-group="${escapeHtml(name)}" data-group-school="${escapeHtml(sample.ma_truong || '')}" data-group-year="${escapeHtml(sample.nam || '')}" title="${escapeHtml(header.title)}">${escapeHtml(header.label)}</button></th>`;
          }).join('')}
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>`;
  }).join('') || '<p class="small text-muted p-3 mb-0">Chưa có ngành và hình thức tuyển sinh</p>';
  renderMethodDocuments();
  const schools = new Set(methodRows.map(row => row.ma_truong)).size;
  const majors = methodRows.filter(row => row.ma_nganh || row.ten_nganh).length;
  const shown = visible.length;
  document.getElementById('methodInfo').textContent = methodRows.length
    ? (shown === methodRows.length
      ? `${majors || methodRows.length} ngành · ${schools} trường`
      : `${shown}/${methodRows.length} dòng đang hiện · ${schools} trường`)
    : 'Chưa có ngành và hình thức tuyển sinh';
  document.querySelectorAll('.method-data-table').forEach(table => {
    const boxes = [...table.querySelectorAll('.method-pick')];
    const checked = boxes.filter(box => box.checked).length;
    const all = table.querySelector('.method-check-all');
    if (!all) return;
    all.checked = boxes.length > 0 && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  });
  document.getElementById('btnDeleteMethods').disabled = selectedMethodKeys.size === 0;
  syncSchoolMethodDelete();
}

function showMethodDetail(row) {
  if (!row) return;
  document.getElementById('methodDetailCaption').textContent = [
    codeName(row.ma_truong, row.ten_truong),
    row.nam ? `Năm ${row.nam}` : '',
  ].filter(Boolean).join(' · ');
  const forms = Array.isArray(row.hinh_thuc) ? row.hinh_thuc : [];
  const program = [
    row.ma_xet_tuyen,
    row.ten_chuong_trinh,
    [row.ma_nganh, row.ten_nganh].filter(Boolean).join(' '),
    row.chi_tieu ? `Chỉ tiêu ${row.chi_tieu}` : '',
  ].filter(Boolean).join(' · ');
  const blocks = forms.map(item => {
    const quote = item.mo_ta ? `<div class="small mt-1">${escapeHtml(item.mo_ta)}</div>` : '';
    return `
      <div class="border rounded p-2 mb-2">
        <strong>${escapeHtml(item.ten || item.id || 'Hình thức')}${item.id && item.ten && item.id !== item.ten ? ` (${escapeHtml(item.id)})` : ''}</strong>
        ${quote}
      </div>`;
  }).join('');
  const link = row.nguon
    ? `<div class="small mt-2"><a href="${escapeHtml(row.nguon)}" target="_blank" rel="noopener">${escapeHtml(row.nguon)}</a></div>`
    : '';
  document.getElementById('methodDetailBody').innerHTML = `
    ${program ? `<p class="mb-2">${escapeHtml(program)}</p>` : ''}
    ${blocks || '<p class="text-muted mb-0">Chưa bóc được hình thức tuyển sinh.</p>'}
    ${link}
    ${row.ghi_chu ? `<p class="small text-muted mb-0 mt-2">${escapeHtml(row.ghi_chu)}</p>` : ''}`;
  bootstrap.Modal.getOrCreateInstance(document.getElementById('methodDetailModal')).show();
}

async function deleteMethodPayload(body, successText) {
  const msg = document.getElementById('collectMsg');
  const res = await fetch('/api/phuong-thuc/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Không xoá được');
  renderMethods(data.records || [], data.documents || []);
  msg.innerHTML = `<div class="alert alert-success py-2 mb-0">${successText}</div>`;
}

async function deleteMethodKeys(keys) {
  const unique = [...new Set(keys.filter(Boolean))];
  if (!unique.length) return;
  const accepted = await confirmDeleteResults(
    unique.length === 1
      ? 'Xoá dòng đã chọn? Nếu trường và năm học không còn dữ liệu, file đã tải lên cũng bị xoá.'
      : `Xoá ${unique.length} dòng đã chọn? Nếu trường và năm học không còn dữ liệu, file đã tải lên cũng bị xoá.`
  );
  if (!accepted) return;
  const msg = document.getElementById('collectMsg');
  try {
    await deleteMethodPayload({
      keys: unique.map(key => {
        const [ma_truong, nam, ma_xet_tuyen, ma_nganh, ten_nganh] = key.split('|');
        return {ma_truong, nam: Number(nam), ma_xet_tuyen, ma_nganh, ten_nganh};
      }),
    }, `Đã xoá ${unique.length} dòng phương thức.`);
    unique.forEach(key => selectedMethodKeys.delete(key));
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
}

async function deleteMethodDocument(doc) {
  const stored = String(doc.stored_name || '').trim();
  const school = String(doc.ma_truong || '').trim();
  if (!stored || !school) return;
  const accepted = await confirmDeleteResults(
    `Xoá tài liệu ${doc.filename || stored} và dữ liệu đã nhập từ file này?`
  );
  if (!accepted) return;
  const msg = document.getElementById('collectMsg');
  try {
    await deleteMethodPayload({
      documents: [{
        ma_truong: school,
        nam: Number(doc.nam || 0),
        stored_name: stored,
      }],
    }, 'Đã xoá tài liệu và dữ liệu đã nhập từ file đó.');
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
}

document.getElementById('btnCollect').addEventListener('click', async () => {
  if (collectBusy) return;
  const codes = window.App.getSelectValues(document.getElementById('schoolPick'));
  const years = selectedYears();
  const msg = document.getElementById('collectMsg');
  if (!codes.length) {
    msg.innerHTML = '<div class="alert alert-warning py-2 mb-0">Hãy chọn ít nhất một trường.</div>';
    return;
  }
  if (!years.length) {
    msg.innerHTML = '<div class="alert alert-warning py-2 mb-0">Hãy chọn ít nhất một năm học.</div>';
    return;
  }
  setCollectBusy(true);
  msg.innerHTML = '';
  setProgress('run', `Đang thu thập phương thức 0/${codes.length} trường…`, 0);
  try {
    const res = await fetch('/api/phuong-thuc/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ codes: codes.join(','), years, merge: true }),
    });
    if (!res.ok || !res.body) throw new Error(`Không thu thập được (HTTP ${res.status})`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let donePayload = null;
    const schoolEvents = [];
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === 'school') {
          schoolEvents.push(event);
          const pct = event.total ? Math.round(event.index * 90 / event.total) : 0;
          setProgress('run', event.log || `${event.code}: ${event.index}/${event.total}`, pct);
        } else if (event.type === 'done') {
          donePayload = event;
        } else if (event.type === 'error') {
          throw new Error(event.error || 'Thu thập phương thức thất bại');
        }
      }
    }
    const saved = await fetch('/api/phuong-thuc/records');
    const data = await saved.json();
    if (!data.ok) throw new Error(data.error || 'Không đọc được phương thức');
    if (data.nhom_phuong_thuc) methodGroups = data.nhom_phuong_thuc;
    renderMethods((donePayload && donePayload.records) || data.records || [], data.documents || []);
    const found = schoolEvents.filter(event => (event.found || []).length).length;
    setProgress('done', 'Thu thập phương thức hoàn tất', 100);
    const programs = schoolEvents.reduce((sum, event) => sum + (event.programs || 0), 0);
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Đã thu <b>${programs}</b> ngành của <b>${found}/${schoolEvents.length}</b> trường, kèm hình thức xét tuyển của từng ngành.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
    setProgress('hide');
  } finally {
    setCollectBusy(false);
  }
});

document.getElementById('methodTables').addEventListener('change', (event) => {
  const box = event.target.closest('.method-pick');
  if (box) {
    if (box.checked) selectedMethodKeys.add(box.dataset.key);
    else selectedMethodKeys.delete(box.dataset.key);
  }
  if (event.target.classList.contains('method-check-all')) {
    const table = event.target.closest('table');
    table.querySelectorAll('.method-pick').forEach(item => {
      item.checked = event.target.checked;
      if (event.target.checked) selectedMethodKeys.add(item.dataset.key);
      else selectedMethodKeys.delete(item.dataset.key);
    });
  }
  document.getElementById('btnDeleteMethods').disabled = selectedMethodKeys.size === 0;
  syncSchoolMethodDelete();
});

document.getElementById('methodTables').addEventListener('click', (event) => {
  const group = event.target.closest('[data-method-group]');
  if (group) {
    openMethodGroup(group.dataset.methodGroup, group.dataset.groupSchool, group.dataset.groupYear);
    return;
  }
  const detail = event.target.closest('[data-method-detail]');
  if (detail) {
    showMethodDetail(methodRows[Number(detail.dataset.methodDetail)]);
    return;
  }
  const del = event.target.closest('[data-delete-method]');
  if (del) deleteMethodKeys([del.dataset.deleteMethod]);
});

document.getElementById('btnDeleteMethods').addEventListener('click', () => {
  deleteMethodKeys([...selectedMethodKeys]);
});

function syncSchoolMethodDelete() {
  const button = document.getElementById('btnDeleteSchoolMethods');
  if (button) button.disabled = checkedValues('methodSchool').length !== 1;
}

function methodYearsForSchool(school) {
  const picked = checkedValues('methodYear').map(Number).filter(Boolean);
  if (picked.length) return picked;
  return [...new Set(methodRows
    .filter(row => String(row.ma_truong || '').trim().toUpperCase() === school)
    .map(row => Number(row.nam) || 0)
    .filter(Boolean))].sort((a, b) => b - a);
}

document.getElementById('btnDeleteSchoolMethods').addEventListener('click', async () => {
  const schools = checkedValues('methodSchool');
  if (schools.length !== 1) return;
  const school = schools[0];
  const years = methodYearsForSchool(school);
  const msg = document.getElementById('collectMsg');
  if (!years.length) {
    msg.innerHTML = `<div class="alert alert-warning py-2 mb-0">Không có dữ liệu phương thức của ${escapeHtml(school)} để xoá.</div>`;
    return;
  }
  const yearLabel = years.length === 1 ? `năm ${years[0]}` : `các năm ${years.join(', ')}`;
  const accepted = await confirmDeleteResults(
    `Xoá toàn bộ phương thức của ${school} ${yearLabel}? Dữ liệu các trường khác được giữ nguyên.`
  );
  if (!accepted) return;
  try {
    await deleteMethodPayload({
      keys: years.map(nam => ({ma_truong: school, nam})),
    }, `Đã xoá phương thức của ${escapeHtml(school)} ${escapeHtml(yearLabel)}.`);
    selectedMethodKeys.clear();
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
});

document.getElementById('methodDocs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-delete-method-doc]');
  if (!button) return;
  deleteMethodDocument({
    stored_name: button.dataset.deleteMethodDoc,
    ma_truong: button.dataset.docSchool,
    nam: button.dataset.docYear,
    filename: button.closest('span')?.innerText || '',
  });
});

document.getElementById('methodQuery').addEventListener('input', () => {
  renderMethods(methodRows);
});
['methodSchool', 'methodYear', 'methodMajor', 'methodForm'].forEach(id => {
  document.getElementById(id).addEventListener('change', () => renderMethods(methodRows));
});

let methodUploadCode = '';

function fillMethodUploadSchools(preferred) {
  const select = document.getElementById('methodUploadSchool');
  const source = document.getElementById('schoolPick');
  const html = [...source.options]
    .filter(option => option.value)
    .map(option => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.textContent)}</option>`)
    .join('');
  const wanted = String(preferred || '').trim().toUpperCase();
  const known = new Set([...source.options].map(option => option.value));
  window.App.setSelectOptions(
    select,
    html,
    known.has(wanted) ? wanted : '',
    { placeholder: 'Chọn trường' },
  );
  methodUploadCode = select.value || '';
}

function syncMethodUploadYearBlocks() {
  const host = document.getElementById('methodUploadYearBlocks');
  const years = checkedValues('methodUploadYear')
    .map(value => String(value))
    .sort((a, b) => Number(b) - Number(a));
  const keep = new Set(years);
  [...host.querySelectorAll('[data-method-upload-year]')].forEach(block => {
    if (!keep.has(block.dataset.methodUploadYear)) block.remove();
  });
  if (!years.length) {
    host.innerHTML = '<p class="small text-muted mb-0">Chọn năm học để tải tài liệu của từng năm.</p>';
    return;
  }
  const hint = host.querySelector('p');
  if (hint) hint.remove();
  years.forEach(year => {
    let block = host.querySelector(`[data-method-upload-year="${year}"]`);
    if (!block) {
      host.insertAdjacentHTML('beforeend', `
        <div class="border rounded p-3" data-method-upload-year="${escapeHtml(year)}">
          <label class="form-label" for="methodUploadFiles${year}">Tài liệu năm ${escapeHtml(year)}</label>
          <input class="form-control" type="file" id="methodUploadFiles${year}" multiple
            accept=".json,.xlsx,.xls,.csv,.pdf,.docx,.html,.htm">
          <div class="form-text">Tuỳ chọn: PDF, Word, Excel, HTML hoặc file JSON của đề án tuyển sinh.</div>
        </div>`);
      block = host.querySelector(`[data-method-upload-year="${year}"]`);
    }
    host.appendChild(block);
  });
}

function openMethodUpload() {
  const selected = checkedValues('methodSchool');
  const picked = checkedValues('schoolPick');
  document.getElementById('methodUploadYearBlocks').innerHTML = '';
  document.getElementById('methodUploadJson').value = '';
  document.getElementById('methodUploadProgress').classList.add('d-none');
  const result = document.getElementById('methodUploadResult');
  result.className = 'alert d-none mt-3 mb-0';
  result.innerHTML = '';
  fillMethodUploadSchools(selected.length === 1 ? selected[0] : (picked.length === 1 ? picked[0] : ''));
  const yearSelect = document.getElementById('methodUploadYear');
  const preferred = checkedValues('methodYear');
  const widget = window.App.getMultiSelect(yearSelect);
  if (widget) widget.setValues(preferred.length ? preferred : ['2026']);
  syncMethodUploadYearBlocks();
  document.getElementById('methodUploadStart').disabled = false;
  document.getElementById('methodUploadClose').disabled = false;
  bootstrap.Modal.getOrCreateInstance(document.getElementById('methodUploadModal')).show();
}

document.getElementById('btnMethodUpload').addEventListener('click', () => openMethodUpload());
document.getElementById('methodUploadSchool').addEventListener('change', () => {
  methodUploadCode = document.getElementById('methodUploadSchool').value || '';
});
document.getElementById('methodUploadYear').addEventListener('change', () => syncMethodUploadYearBlocks());

function setMethodUploadProgress(pct, status) {
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  document.getElementById('methodUploadProgress').classList.remove('d-none');
  document.getElementById('methodUploadStatus').textContent = status || '';
  document.getElementById('methodUploadPct').textContent = `${p}%`;
  const bar = document.getElementById('methodUploadBar');
  bar.style.width = `${p}%`;
  bar.textContent = `${p}%`;
}

document.getElementById('methodUploadStart').addEventListener('click', async () => {
  const result = document.getElementById('methodUploadResult');
  const jobs = [...document.querySelectorAll('#methodUploadYearBlocks [data-method-upload-year]')].map(block => ({
    year: block.dataset.methodUploadYear,
    files: [...block.querySelector('input[type="file"]').files],
  }));
  const fileJobs = jobs.filter(job => job.files.length);
  const rawJson = document.getElementById('methodUploadJson').value.trim();
  if (!methodUploadCode) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Hãy chọn trường.';
    return;
  }
  if (!jobs.length) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Hãy chọn ít nhất một năm học.';
    return;
  }
  if (!rawJson && !fileJobs.length) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Hãy dán JSON hoặc chọn tài liệu cho ít nhất một năm học.';
    return;
  }
  let rows = null;
  if (rawJson) {
    try {
      rows = JSON.parse(rawJson);
    } catch (_) {
      result.className = 'alert alert-warning mt-3 mb-0';
      result.textContent = 'Dữ liệu không phải JSON hợp lệ.';
      return;
    }
  }
  const tooLarge = fileJobs.flatMap(job => job.files).find(file => file.size > 20 * 1024 * 1024);
  if (tooLarge) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = `${tooLarge.name} vượt quá 20 MB.`;
    return;
  }
  document.getElementById('methodUploadStart').disabled = true;
  document.getElementById('methodUploadClose').disabled = true;
  result.className = 'alert d-none mt-3 mb-0';
  let filesRead = 0;
  let programs = 0;
  let majors = 0;
  const errors = [];
  const steps = (rows ? 1 : 0) + fileJobs.length;
  let done = 0;
  try {
    if (rows) {
      setMethodUploadProgress(10, 'Đang nhập JSON…');
      const res = await fetch('/api/phuong-thuc/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          code: methodUploadCode,
          years: jobs.map(job => Number(job.year)),
          rows,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Không nhập được JSON');
      programs += data.programs || 0;
      majors = data.majors || 0;
      renderMethods(data.records || [], data.documents || []);
      done += 1;
    }
    for (let index = 0; index < fileJobs.length; index += 1) {
      const job = fileJobs[index];
      setMethodUploadProgress(((done + index) / steps) * 80, `Đang đọc năm ${job.year}…`);
      const form = new FormData();
      form.append('code', methodUploadCode);
      form.append('year', job.year);
      job.files.forEach(file => form.append('files', file));
      const res = await fetch('/api/phuong-thuc/upload', { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || `Không đọc được tài liệu năm ${job.year}`);
      filesRead += data.files || 0;
      programs += data.programs || 0;
      (data.errors || []).forEach(message => errors.push(message));
      renderMethods(data.records || [], data.documents || []);
    }
    setMethodUploadProgress(100, 'Đã cập nhật phương thức tuyển sinh');
    const warning = errors.length ? `<div class="mt-1">${escapeHtml(errors.join('; '))}</div>` : '';
    const jsonNote = rows
      ? `Đã nhập JSON cho <b>${majors}</b> ngành, <b>${jobs.length}</b> năm học`
      : '';
    const fileNote = filesRead
      ? `Đã đọc <b>${filesRead}</b> tài liệu`
      : '';
    result.className = 'alert alert-success mt-3 mb-0';
    result.innerHTML = `${[jsonNote, fileNote].filter(Boolean).join('. ')}. Cập nhật <b>${programs}</b> dòng ngành.${warning}`;
  } catch (e) {
    setMethodUploadProgress(100, 'Không cập nhật được');
    result.className = 'alert alert-danger mt-3 mb-0';
    result.textContent = e.message;
  } finally {
    document.getElementById('methodUploadStart').disabled = false;
    document.getElementById('methodUploadClose').disabled = false;
  }
});


loadCollectSchools().then(async () => {
  try {
    const saved = await fetch('/api/phuong-thuc/records');
    if (!saved.ok) return;
    const data = await saved.json();
    if (!data.ok) return;
    if (data.nhom_phuong_thuc) methodGroups = data.nhom_phuong_thuc;
    renderMethods(data.records || [], data.documents || []);
  } catch (_) {}
});
