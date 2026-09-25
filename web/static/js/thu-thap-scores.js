var resultGroups = [];
var methodsBySchool = {};
var resultDownloadUrl = '';
var resultYears = [];
var selectedResultKeys = new Set();
function resultKey(group) {
  return [
    group.ma_truong || '',
    group.ma_xet_tuyen || '',
    group.ma_nganh || group.ten_nganh || '',
  ].join('|');
}

function selectedResultYear() {
  return Number(window.App.getSelectValues(document.getElementById('resultYear'))[0]) || 0;
}

function scoreMethodMatches(row, selected) {
  if (!selected.size) return true;
  const name = String(row.phuong_thuc || '').trim();
  if (!name) return false;
  if (selected.has(name)) return true;
  return methodGroupCodes(row.ma_truong, row.nam, name).some(code => selected.has(code));
}

function scoreHeaderNote(name, rows, year) {
  const schools = [...new Set((rows || [])
    .filter(row => String(row.phuong_thuc || '').trim() === name)
    .map(row => String(row.ma_truong || '').trim().toUpperCase())
    .filter(Boolean))];
  if (schools.length === 1) return {note: methodColumnNote(name, schools[0], year), school: schools[0]};
  const notes = schools.map(school => methodColumnNote(name, school, year));
  const titles = [...new Set(notes.map(note => note.title))];
  return {
    note: {kind: titles.length === 1 ? notes[0].kind : 'mixed', title: titles.join(' | ') || name},
    school: '',
  };
}

function scoreColumnNames(rows) {
  const selected = new Set(checkedValues('resultMethod'));
  const names = [];
  (rows || []).forEach(row => {
    const name = String(row.phuong_thuc || '').trim();
    if (!name || names.includes(name) || !scoreMethodMatches(row, selected)) return;
    names.push(name);
  });
  const officialOrder = OFFICIAL_METHODS.map(item => item.ma);
  return [
    ...officialOrder.filter(code => names.includes(code)),
    ...names.filter(name => !officialMethod(name)).sort((a, b) => a.localeCompare(b, 'vi')),
  ];
}

function majorByAdmissionCode(school, year, code) {
  const wanted = String(code || '').trim().toUpperCase();
  const schoolCode = String(school || '').trim().toUpperCase();
  if (!wanted || !schoolCode) return null;
  const matches = methodRows.filter(row =>
    String(row.ma_truong || '').trim().toUpperCase() === schoolCode
    && String(row.ma_xet_tuyen || '').trim().toUpperCase() === wanted
  );
  return matches.find(row => String(row.nam || '') === String(year || '')) || matches[0] || null;
}

function scoreMajorLabel(group) {
  const hit = majorByAdmissionCode(group.ma_truong, selectedResultYear(), group.ma_xet_tuyen)
    || majorByAdmissionCode(group.ma_truong, '', group.ma_xet_tuyen);
  const code = group.ma_xet_tuyen || group.ma_nganh;
  const name = (hit && (hit.ten_nganh || hit.ten_chuong_trinh)) || group.ten_nganh || '';
  return codeName(code, name);
}

function editCell(key, year, method, value, title) {
  const shown = value == null || value === '' ? '—' : value;
  const stored = value == null || value === '' ? '' : value;
  const hint = title || 'Nhấp đúp để sửa';
  return `<td class="text-center result-edit" data-key="${escapeHtml(key)}" data-year="${year}" data-method="${escapeHtml(method)}" data-field="diem" data-value="${escapeHtml(stored)}" title="${escapeHtml(hint)}">${escapeHtml(shown)}</td>`;
}

function parseEditValue(field, raw) {
  const text = String(raw ?? '').trim().replace(',', '.');
  if (!text || text === '—') return null;
  const num = Number(text);
  if (!Number.isFinite(num)) throw new Error('Giá trị không hợp lệ.');
  if (field === 'chi_tieu') return Math.round(num);
  return Math.round(num * 100) / 100;
}

function refreshResultMethods() {
  const methodHtml = OFFICIAL_METHODS
    .map(item => `<option value="${escapeHtml(item.ma)}">${escapeHtml(item.ma)} — ${escapeHtml(item.ten)}</option>`)
    .join('');
  const keep = window.App.getSelectValues(document.getElementById('resultMethod'))
    .filter(code => officialMethod(code));
  window.App.setSelectOptions(document.getElementById('resultMethod'), methodHtml, keep, {
    placeholder: 'Tất cả phương thức',
  });
}

function syncResultFilters(rows) {
  const schools = new Map();
  const majors = new Map();
  const years = new Set();
  (rows || []).forEach(row => {
    const school = String(row.ma_truong || '').trim();
    if (school) schools.set(school, codeName(school, row.ten_truong));
    const major = String(row.ma_nganh || row.ten_nganh || '').trim();
    if (major) majors.set(major, codeName(row.ma_nganh, row.ten_nganh));
    if (row.nam) years.add(String(row.nam));
  });
  const keep = (id) => window.App.getSelectValues(document.getElementById(id));
  const yearList = [...years].sort((a, b) => Number(b) - Number(a));
  const currentYear = String(selectedResultYear() || '');
  const chosenYear = yearList.includes(currentYear) ? currentYear : (yearList[0] || '');
  window.App.setSelectOptions(document.getElementById('resultSchool'), resultOptionHtml([...schools]), keep('resultSchool'), {
    placeholder: 'Tất cả trường',
  });
  window.App.setSelectOptions(
    document.getElementById('resultYear'),
    yearList.map(year => `<option value="${escapeHtml(year)}">${escapeHtml(year)}</option>`).join(''),
    chosenYear,
    { placeholder: 'Chọn năm' },
  );
  window.App.setSelectOptions(document.getElementById('resultMajor'), resultOptionHtml([...majors]), keep('resultMajor'), {
    placeholder: 'Tất cả ngành',
  });
  refreshResultMethods();
}

function filteredResultRows() {
  const schools = new Set(checkedValues('resultSchool'));
  const majors = new Set(checkedValues('resultMajor'));
  const methods = new Set(checkedValues('resultMethod'));
  const year = selectedResultYear();
  return resultRows.filter(row => {
    if (year && Number(row.nam) !== year) return false;
    if (schools.size && !schools.has(String(row.ma_truong || '').trim())) return false;
    const major = String(row.ma_nganh || row.ten_nganh || '').trim();
    if (majors.size && !majors.has(major)) return false;
    if (!scoreMethodMatches(row, methods)) return false;
    return true;
  });
}

function renderResults(rows, downloadUrl, years, methods) {
  if (rows) {
    resultRows = rows;
    resultDownloadUrl = downloadUrl || '';
    resultYears = years || [];
    if (methods) methodsBySchool = methods;
    syncResultFilters(resultRows);
  }
  const source = filteredResultRows();
  const year = selectedResultYear();
  const methodNames = scoreColumnNames(source);
  const head = document.getElementById('resultHead');
  const span = ' rowspan="2"';
  const methodHeads = methodNames.length
    ? methodNames.map(name => {
      const header = scoreHeaderNote(name, source, year);
      if (header.note.kind === 'official' || !header.school) {
        return `<th class="text-center" title="${escapeHtml(header.note.title)}">${escapeHtml(name)}</th>`;
      }
      return `<th class="text-center"><button type="button" class="btn btn-link btn-sm p-0 text-decoration-none" data-method-group="${escapeHtml(name)}" data-group-school="${escapeHtml(header.school)}" data-group-year="${escapeHtml(year)}" title="${escapeHtml(header.note.title)}">${escapeHtml(name)}</button></th>`;
    }).join('')
    : '<th class="text-center">—</th>';
  head.innerHTML = `<tr>
    <th class="text-center"${span}><input class="form-check-input" type="checkbox" id="resultCheckAll" aria-label="Chọn tất cả dòng kết quả"></th>
    <th${span}>#</th><th${span}>Trường</th><th${span}>Ngành tuyển sinh</th>
    <th class="text-center" colspan="${methodNames.length || 1}">Điểm chuẩn${year ? ` ${year}` : ''}</th>
    <th${span}>Thời gian thu thập</th><th${span}>Thao tác</th>
  </tr><tr>${methodHeads}</tr>`;

  const groups = new Map();
  source.forEach(row => {
    const key = [
      row.ma_truong || '',
      row.ma_xet_tuyen || '',
      row.ma_nganh || row.ten_nganh || '',
    ].join('|');
    if (!groups.has(key)) {
      groups.set(key, {
        ma_truong: row.ma_truong || '',
        ten_truong: row.ten_truong || '',
        ma_xet_tuyen: row.ma_xet_tuyen || '',
        ma_nganh: row.ma_nganh || '',
        ten_nganh: row.ten_nganh || '',
        byMethod: {},
        rawMethods: [],
        thu_thap_luc: '',
      });
    }
    const group = groups.get(key);
    if (row.thu_thap_luc && String(row.thu_thap_luc) > group.thu_thap_luc) {
      group.thu_thap_luc = String(row.thu_thap_luc);
    }
    const method = String(row.phuong_thuc || '').trim();
    if (!method || !methodNames.includes(method)) return;
    if (!group.rawMethods.includes(method)) group.rawMethods.push(method);
    const cell = group.byMethod[method] || {diem: null, sources: []};
    if (!cell.sources.includes(method)) cell.sources.push(method);
    if (row.diem_chuan != null && row.diem_chuan !== '') cell.diem = row.diem_chuan;
    group.byMethod[method] = cell;
  });

  const list = [...groups.values()];
  resultGroups = list;
  const alive = new Set(list.map(resultKey));
  [...selectedResultKeys].forEach(key => {
    if (!alive.has(key)) selectedResultKeys.delete(key);
  });
  document.querySelector('#resultTable tbody').innerHTML = list.map((group, i) => {
    const key = resultKey(group);
    const checked = selectedResultKeys.has(key) ? 'checked' : '';
    const scoreCells = methodNames.length
      ? methodNames.map(code => {
        const cell = group.byMethod[code] || {diem: null, sources: []};
        return editCell(key, year, code, cell.diem, methodColumnNote(code, group.ma_truong, year).title);
      }).join('')
      : '<td class="text-center text-muted">—</td>';
    return `
    <tr>
      <td class="text-center"><input class="form-check-input result-pick" type="checkbox" data-key="${escapeHtml(key)}" aria-label="Chọn dòng ${i + 1}" ${checked}></td>
      <td class="text-muted">${i + 1}</td>
      <td class="text-nowrap">${escapeHtml(codeName(group.ma_truong, group.ten_truong))}</td>
      <td class="result-major">${escapeHtml(scoreMajorLabel(group))}</td>
      ${scoreCells}
      <td class="text-nowrap">${escapeHtml(formatCollectedAt(group.thu_thap_luc))}</td>
      <td class="text-nowrap">
        <button type="button" class="btn btn-outline-danger btn-sm" data-delete-result="${escapeHtml(key)}" title="Xoá dòng này">
          <i class="bi bi-trash3"></i>
        </button>
      </td>
    </tr>`;
  }).join('');
  document.getElementById('resultInfo').textContent = list.length
    ? `${list.length} ngành${year ? ` · năm ${year}` : ''}`
    : (resultRows.length ? 'Không có dòng khớp bộ lọc' : 'Chưa có kết quả');
  updateResultSelect();
  const excel = document.getElementById('btnExcel');
  if (resultDownloadUrl && resultRows.length) {
    excel.href = resultDownloadUrl;
    excel.classList.remove('d-none');
  } else {
    excel.classList.add('d-none');
  }
}

document.getElementById('resultSchool').addEventListener('change', () => {
  refreshResultMethods();
  renderResults();
});
['resultYear', 'resultMajor', 'resultMethod'].forEach(id => {
  document.getElementById(id).addEventListener('change', () => renderResults());
});
document.getElementById('resultHead').addEventListener('click', (event) => {
  const group = event.target.closest('[data-method-group]');
  if (!group) return;
  openMethodGroup(group.dataset.methodGroup, group.dataset.groupSchool, group.dataset.groupYear);
});

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
  setProgress('run', `Đang thu thập 0/${codes.length} trường…`, 0);
  try {
    const res = await fetch('/api/crawl/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ codes: codes.join(','), years, merge: true })
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
        } else if (event.type === 'finalize') {
          setProgress('run', event.message || 'Đang lưu kết quả…', 95);
        } else if (event.type === 'done') {
          donePayload = event;
        } else if (event.type === 'error') {
          throw new Error(event.error || 'Thu thập thất bại');
        }
      }
    }
    const saved = await fetch('/api/crawl/records');
    const data = await saved.json();
    if (!data.ok) throw new Error(data.error || 'Không đọc được kết quả');
    renderResults(data.rows || [], (donePayload && donePayload.download_url) || data.download_url || '', data.years || years, data.methods_by_school || {});
    const collected = schoolEvents.reduce((sum, event) => sum + Number(event.admission_count || 0), 0);
    const empty = schoolEvents.filter(event => !event.admission_count);
    setProgress('done', 'Thu thập hoàn tất', 100);
    if (schoolEvents.length && !collected) {
      const detail = empty.map(event => escapeHtml(event.log || event.code)).join('<br>');
      msg.innerHTML = `<div class="alert alert-warning py-2 mb-0">Không thu được dòng điểm nào cho trường đã chọn.<br>${detail}</div>`;
    } else if (empty.length) {
      const detail = empty.map(event => escapeHtml(event.log || event.code)).join('<br>');
      msg.innerHTML = `<div class="alert alert-warning py-2 mb-0">Thu thập được <b>${collected}</b> dòng. Một số trường không có điểm:<br>${detail}</div>`;
    } else {
      msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Thu thập thành công <b>${collected}</b> dòng ngành, điểm chuẩn theo phương thức và chỉ tiêu theo năm.</div>`;
    }
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
    setProgress('hide');
  } finally {
    setCollectBusy(false);
  }
});

function updateResultSelect() {
  const boxes = [...document.querySelectorAll('.result-pick')];
  const checked = boxes.filter(box => box.checked).length;
  const all = document.getElementById('resultCheckAll');
  if (all) {
    all.checked = boxes.length > 0 && checked === boxes.length;
    all.indeterminate = checked > 0 && checked < boxes.length;
  }
  document.getElementById('btnDeleteResults').disabled = selectedResultKeys.size === 0;
  const schoolDelete = document.getElementById('btnDeleteSchoolScores');
  if (schoolDelete) schoolDelete.disabled = checkedValues('resultSchool').length !== 1 || !selectedResultYear();
}

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

async function deleteResultKeys(keys) {
  const unique = [...new Set((keys || []).filter(Boolean))];
  if (!unique.length) return;
  const label = unique.length === 1 ? 'dòng đã chọn' : `${unique.length} dòng đã chọn`;
  const accepted = await confirmDeleteResults(`Xoá ${label} khỏi dữ liệu đã tải?`);
  if (!accepted) return;
  const payload = unique.flatMap(key => {
    const group = resultGroups.find(item => resultKey(item) === key);
    if (!group) return [];
    const methods = group.rawMethods && group.rawMethods.length
      ? group.rawMethods
      : Object.keys(group.byMethod || {});
    return (methods.length ? methods : ['']).map(method => ({
      ma_truong: group.ma_truong,
      ma_xet_tuyen: group.ma_xet_tuyen,
      ma_nganh: group.ma_nganh,
      ten_nganh: group.ten_nganh,
      phuong_thuc: method,
      nam: selectedResultYear(),
    }));
  });
  const msg = document.getElementById('collectMsg');
  try {
    const res = await fetch('/api/crawl/records/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ keys: payload }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Không xoá được');
    unique.forEach(key => selectedResultKeys.delete(key));
    renderResults(data.rows || [], data.download_url || '', data.years || [], data.methods_by_school || {});
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Đã xoá ${unique.length} dòng khỏi dữ liệu đã tải.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
}

document.getElementById('resultTable').addEventListener('change', (event) => {
  const all = event.target.id === 'resultCheckAll' ? event.target : null;
  if (all) {
    document.querySelectorAll('.result-pick').forEach(box => {
      box.checked = all.checked;
      const key = box.dataset.key || '';
      if (all.checked) selectedResultKeys.add(key);
      else selectedResultKeys.delete(key);
    });
    updateResultSelect();
    return;
  }
  const box = event.target.closest('.result-pick');
  if (!box) return;
  const key = box.dataset.key || '';
  if (box.checked) selectedResultKeys.add(key);
  else selectedResultKeys.delete(key);
  updateResultSelect();
});

function openSourceModal(key) {
  const group = resultGroups.find(item => resultKey(item) === key);
  if (!group) return;
  const urls = sourceUrls(group.sources);
  document.getElementById('resultSourceCaption').textContent = [
    codeName(group.ma_truong, group.ten_truong),
    codeName(group.ma_nganh, group.ten_nganh),
    group.phuong_thuc || '',
  ].filter(Boolean).join(' · ');
  document.getElementById('resultSourceList').innerHTML = urls.length
    ? urls.map(url => `<li class="mb-1"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a></li>`).join('')
    : '<li>Không có liên kết.</li>';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('resultSourceModal')).show();
}

function showEditedCell(td, value) {
  const shown = value == null || value === '' ? '—' : String(value);
  td.dataset.value = value == null || value === '' ? '' : String(value);
  td.textContent = shown;
}

async function saveEditedCell(td, raw) {
  const group = resultGroups.find(item => resultKey(item) === (td.dataset.key || ''));
  if (!group) throw new Error('Không tìm thấy dòng cần sửa.');
  const field = td.dataset.field;
  const year = Number(td.dataset.year);
  const method = td.dataset.method || '';
  const value = parseEditValue(field, raw);
  const res = await fetch('/api/crawl/records/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      ma_truong: group.ma_truong,
      ma_xet_tuyen: group.ma_xet_tuyen,
      ma_nganh: group.ma_nganh,
      ten_nganh: group.ten_nganh,
      phuong_thuc: method,
      nam: year,
      field,
      value,
    }),
  });
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Không lưu được');
  const cell = group.byMethod[method] || {diem: null, sources: []};
  cell.diem = value;
  if (!cell.sources.includes(method)) cell.sources.push(method);
  group.byMethod[method] = cell;
  showEditedCell(td, value);
}

function beginCellEdit(td) {
  if (!td || td.querySelector('input')) return;
  const previous = td.dataset.value || '';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'form-control form-control-sm text-center px-1';
  input.value = previous;
  input.setAttribute('aria-label', 'Sửa giá trị');
  td.textContent = '';
  td.appendChild(input);
  input.focus();
  input.select();
  let closed = false;
  const close = async (save) => {
    if (closed) return;
    closed = true;
    if (!save || input.value.trim() === previous) {
      showEditedCell(td, previous);
      return;
    }
    try {
      await saveEditedCell(td, input.value);
    } catch (e) {
      showEditedCell(td, previous);
      document.getElementById('collectMsg').innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
    }
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      input.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      input.value = previous;
      input.blur();
    }
  });
  input.addEventListener('blur', () => close(true));
}

document.getElementById('resultTable').addEventListener('dblclick', (event) => {
  const td = event.target.closest('td.result-edit');
  if (!td) return;
  beginCellEdit(td);
});

document.getElementById('resultTable').addEventListener('click', (event) => {
  const sourceBtn = event.target.closest('[data-source-result]');
  if (sourceBtn) {
    openSourceModal(sourceBtn.dataset.sourceResult || '');
    return;
  }
  const btn = event.target.closest('[data-delete-result]');
  if (!btn) return;
  deleteResultKeys([btn.dataset.deleteResult]);
});

document.getElementById('btnDeleteResults').addEventListener('click', () => {
  deleteResultKeys([...selectedResultKeys]);
});

document.getElementById('btnDeleteSchoolScores').addEventListener('click', async () => {
  const schools = checkedValues('resultSchool');
  const year = selectedResultYear();
  if (schools.length !== 1 || !year) return;
  const school = schools[0];
  const accepted = await confirmDeleteResults(
    `Xoá toàn bộ điểm của ${school} năm ${year}? Dữ liệu các trường khác được giữ nguyên.`
  );
  if (!accepted) return;
  const msg = document.getElementById('collectMsg');
  try {
    const res = await fetch('/api/crawl/records/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ schools: [school], nam: year }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Không xoá được');
    selectedResultKeys.clear();
    renderResults(data.rows || [], data.download_url || '', data.years || [], data.methods_by_school || {});
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Đã xoá điểm của ${escapeHtml(school)} năm ${escapeHtml(year)}.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
});

let scoreUploadCode = '';

function fillScoreUploadSchools(preferred) {
  const select = document.getElementById('scoreUploadSchoolSelect');
  const options = new Map();
  (schoolRows || []).forEach(school => {
    const code = String(school.code || '').trim().toUpperCase();
    if (code) options.set(code, `${code} - ${school.name || ''}`);
  });
  (resultRows || []).forEach(row => {
    const code = String(row.ma_truong || '').trim().toUpperCase();
    if (code && !options.has(code)) options.set(code, codeName(code, row.ten_truong));
  });
  const pairs = [...options].sort((a, b) => a[1].localeCompare(b[1], 'vi'));
  const wanted = String(preferred || '').trim().toUpperCase();
  window.App.setSelectOptions(
    select,
    pairs.map(([code, label]) => `<option value="${escapeHtml(code)}">${escapeHtml(label)}</option>`).join(''),
    options.has(wanted) ? wanted : '',
    { placeholder: 'Chọn trường' },
  );
  scoreUploadCode = select.value || '';
  const caption = document.getElementById('scoreUploadSchool');
  const chosen = [...select.selectedOptions][0];
  caption.textContent = chosen ? chosen.textContent : '';
}

function openScoreUpload() {
  const selected = checkedValues('resultSchool');
  document.getElementById('scoreUploadProgress').classList.add('d-none');
  const result = document.getElementById('scoreUploadResult');
  result.className = 'alert d-none mt-3 mb-0';
  result.innerHTML = '';
  fillScoreUploadSchools(selected.length === 1 ? selected[0] : '');
  const preferredYear = resultYears.includes(2026) ? '2026' : String(resultYears[resultYears.length - 1] || 2026);
  const yearSelect = document.getElementById('scoreUploadYear');
  const yearWidget = window.App.getMultiSelect(yearSelect);
  if (yearWidget) yearWidget.setValues([preferredYear]);
  else yearSelect.value = preferredYear;
  document.getElementById('scoreUploadStart').disabled = false;
  document.getElementById('scoreUploadClose').disabled = false;
  bootstrap.Modal.getOrCreateInstance(document.getElementById('scoreUploadModal')).show();
}

document.getElementById('btnScoreUpload').addEventListener('click', () => openScoreUpload());

document.getElementById('scoreUploadSchoolSelect').addEventListener('change', () => {
  const select = document.getElementById('scoreUploadSchoolSelect');
  scoreUploadCode = select.value || '';
  const chosen = [...select.selectedOptions][0];
  document.getElementById('scoreUploadSchool').textContent = chosen
    ? `${chosen.textContent} · mỗi lần nhập một năm học`
    : 'Mỗi lần chỉ nhập một trường trong một năm học.';
});

function setScoreUploadProgress(pct, status) {
  const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
  document.getElementById('scoreUploadProgress').classList.remove('d-none');
  document.getElementById('scoreUploadStatus').textContent = status || '';
  document.getElementById('scoreUploadPct').textContent = `${p}%`;
  const bar = document.getElementById('scoreUploadBar');
  bar.style.width = `${p}%`;
  bar.textContent = `${p}%`;
}

document.getElementById('scoreUploadStart').addEventListener('click', async () => {
  const result = document.getElementById('scoreUploadResult');
  const year = checkedValues('scoreUploadYear')[0] || '';
  const raw = document.getElementById('scoreUploadJson').value.trim();
  if (!scoreUploadCode) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Hãy chọn một trường.';
    return;
  }
  if (!year) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Hãy chọn một năm học.';
    return;
  }
  if (!raw) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Hãy dán dữ liệu điểm.';
    return;
  }
  let rows;
  try {
    rows = JSON.parse(raw);
  } catch (_) {
    result.className = 'alert alert-warning mt-3 mb-0';
    result.textContent = 'Dữ liệu không phải JSON hợp lệ.';
    return;
  }
  document.getElementById('scoreUploadStart').disabled = true;
  document.getElementById('scoreUploadClose').disabled = true;
  result.className = 'alert d-none mt-3 mb-0';
  setScoreUploadProgress(30, 'Đang lưu điểm…');
  try {
    const res = await fetch('/api/crawl/records/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ code: scoreUploadCode, year: Number(year), rows }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Không lưu được dữ liệu điểm');
    setScoreUploadProgress(80, 'Đang cập nhật bảng điểm…');
    const saved = await fetch('/api/crawl/records');
    const table = await saved.json();
    if (table.ok) {
      renderResults(table.rows || [], table.download_url || resultDownloadUrl, table.years || resultYears, table.methods_by_school || {});
    }
    if (data.programs) {
      const methods = await fetch('/api/phuong-thuc/records');
      const methodData = await methods.json();
      if (methodData.ok) renderMethods(methodData.records || [], methodData.documents || []);
    }
    setScoreUploadProgress(100, data.rows ? 'Đã cập nhật điểm chuẩn' : 'Đã cập nhật phương thức');
    const missing = (data.missing || []).filter((code, index, list) => list.indexOf(code) === index);
    const warning = missing.length
      ? `<div class="mt-1">Chưa có tên ngành cho mã: ${escapeHtml(missing.join(', '))}. Hãy nhập phương thức xét tuyển của trường trước.</div>`
      : '';
    const scoreNote = data.rows
      ? `Đã lưu <b>${data.majors || 0}</b> ngành, <b>${data.rows || 0}</b> dòng điểm`
      : '';
    const methodNote = data.programs
      ? `Đã cập nhật <b>${data.programs}</b> ngành, mỗi phương thức dùng chung một tổ hợp`
      : '';
    result.className = 'alert alert-success mt-3 mb-0';
    result.innerHTML = `${[scoreNote, methodNote].filter(Boolean).join('. ')} cho ${escapeHtml(scoreUploadCode)} năm ${escapeHtml(year)}.${warning}`;
  } catch (e) {
    setScoreUploadProgress(100, 'Không lưu được');
    result.className = 'alert alert-danger mt-3 mb-0';
    result.textContent = e.message;
  } finally {
    document.getElementById('scoreUploadStart').disabled = false;
    document.getElementById('scoreUploadClose').disabled = false;
  }
});


loadCollectSchools().then(async () => {
  try {
    const saved = await fetch('/api/phuong-thuc/records');
    if (saved.ok) {
      const data = await saved.json();
      if (data.ok) {
        if (data.nhom_phuong_thuc) methodGroups = data.nhom_phuong_thuc;
        methodRows = data.records || [];
        methodDocuments = data.documents || [];
      }
    }
  } catch (_) {}
  try {
    const saved = await fetch('/api/crawl/records');
    if (!saved.ok) return;
    const data = await saved.json();
    if (data.ok && (data.rows || []).length) {
      renderResults(data.rows, data.download_url || '', data.years || [], data.methods_by_school || {});
    }
  } catch (_) {}
});
