var convertRows = [];
var selectedConvertKeys = new Set();
function visibleConversions(rows) {
  const years = new Set(selectedYears());
  if (!years.size) return rows || [];
  return (rows || []).filter(row => !row.nam || years.has(Number(row.nam)));
}

function renderConversions(rows, downloadUrl) {
  convertRows = rows || [];
  const list = visibleConversions(convertRows);
  const alive = new Set(list.map(convertKey));
  [...selectedConvertKeys].forEach(key => {
    if (!alive.has(key)) selectedConvertKeys.delete(key);
  });
  document.querySelector('#convertTable tbody').innerHTML = list.map((row, i) => {
    const key = convertKey(row);
    const checked = selectedConvertKeys.has(key) ? 'checked' : '';
    return `
    <tr>
      <td class="text-center"><input class="form-check-input convert-pick" type="checkbox" data-key="${escapeHtml(key)}" aria-label="Chọn quy đổi ${i + 1}" ${checked}></td>
      <td class="text-muted">${i + 1}</td>
      <td class="text-nowrap">${escapeHtml(codeName(row.ma_truong, row.ten_truong))}</td>
      <td class="text-center">${escapeHtml(row.nam || '—')}</td>
      <td>${escapeHtml(row.loai || '—')}</td>
      <td class="convert-body">${escapeHtml(row.noi_dung || '—')}</td>
      <td class="text-nowrap">
        ${sourceUrls([row.nguon]).length
          ? `<button type="button" class="btn btn-outline-secondary btn-sm" data-convert-source="${i}">Chi tiết</button>`
          : '—'}
      </td>
      <td>
        <button type="button" class="btn btn-outline-danger btn-sm" data-delete-convert="${escapeHtml(key)}" title="Xoá dòng này">
          <i class="bi bi-trash3"></i>
        </button>
      </td>
    </tr>`;
  }).join('');
  document.getElementById('convertInfo').textContent = list.length
    ? `${list.length} dòng quy đổi`
    : 'Chưa có bảng quy đổi';
  updateConvertSelect();
  const excel = document.getElementById('btnConvertExcel');
  if (downloadUrl && list.length) {
    excel.href = downloadUrl;
    excel.classList.remove('d-none');
  } else {
    excel.classList.add('d-none');
  }
}

async function collectConversions() {
  const codes = window.App.getSelectValues(document.getElementById('schoolPick'));
  const msg = document.getElementById('collectMsg');
  if (!codes.length) {
    msg.innerHTML = '<div class="alert alert-warning py-2 mb-0">Hãy chọn ít nhất một trường.</div>';
    return;
  }
  setCollectBusy(true);
  msg.innerHTML = '';
  setProgress('run', `Đang thu thập quy đổi 0/${codes.length} trường…`, 0);
  try {
    const res = await fetch('/api/quy-doi/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ codes: codes.join(',') }),
    });
    if (!res.ok || !res.body) throw new Error(`Không thu thập được (HTTP ${res.status})`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let donePayload = null;
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
          const pct = event.total ? Math.round(event.index * 90 / event.total) : 0;
          const note = event.ok
            ? `${event.code}: ${event.rows || 0} dòng quy đổi`
            : (event.error || event.code);
          setProgress('run', note, pct);
        } else if (event.type === 'finalize') {
          setProgress('run', event.message || 'Đang lưu bảng quy đổi…', 95);
        } else if (event.type === 'done') {
          donePayload = event;
        } else if (event.type === 'error') {
          throw new Error(event.error || 'Thu thập quy đổi thất bại');
        }
      }
    }
    const saved = await fetch('/api/quy-doi/records');
    const data = await saved.json();
    if (!data.ok) throw new Error(data.error || 'Không đọc được bảng quy đổi');
    renderConversions(data.rows || [], (donePayload && donePayload.download_url) || data.download_url || '');
    setProgress('done', 'Thu thập quy đổi hoàn tất', 100);
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Thu thập thành công <b>${(data.rows || []).length}</b> dòng quy đổi chứng chỉ và điểm thi THPT.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
    setProgress('hide');
  } finally {
    setCollectBusy(false);
  }
}

function convertKey(row) {
  return [row.ma_truong, row.nam, row.loai, row.noi_dung].join('\u001f');
}
document.getElementById('btnCollect').addEventListener('click', async () => {
  if (collectBusy) return;
  await collectConversions();
});

document.getElementById('convertTable').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-convert-source]');
  if (!btn) return;
  const row = visibleConversions(convertRows)[Number(btn.dataset.convertSource)];
  if (!row) return;
  document.getElementById('resultSourceCaption').textContent = [
    codeName(row.ma_truong, row.ten_truong),
    row.loai || '',
  ].filter(Boolean).join(' · ');
  const urls = sourceUrls([row.nguon]);
  document.getElementById('resultSourceList').innerHTML = urls.length
    ? urls.map(url => `<li class="mb-1"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a></li>`).join('')
    : '<li>Không có liên kết.</li>';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('resultSourceModal')).show();
});

function updateConvertSelect() {
  updatePickSelect('.convert-pick', 'convertCheckAll', 'btnDeleteConvert', selectedConvertKeys);
}
bindPickTable('convertTable', '.convert-pick', 'convertCheckAll', selectedConvertKeys, updateConvertSelect);
async function deleteConvertKeys(keys) {
  const unique = [...new Set((keys || []).filter(Boolean))];
  if (!unique.length) return;
  const label = unique.length === 1 ? 'dòng quy đổi đã chọn' : `${unique.length} dòng quy đổi đã chọn`;
  const accepted = await confirmDeleteResults(`Xoá ${label} khỏi dữ liệu đã tải?`);
  if (!accepted) return;
  const payload = unique.map(key => {
    return visibleConversions(convertRows).find(row => convertKey(row) === key)
      || convertRows.find(row => convertKey(row) === key);
  }).filter(Boolean);
  const msg = document.getElementById('collectMsg');
  try {
    const res = await fetch('/api/quy-doi/records/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ keys: payload }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Không xoá được');
    unique.forEach(key => selectedConvertKeys.delete(key));
    renderConversions(data.rows || [], data.download_url || '');
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Đã xoá ${unique.length} dòng quy đổi.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
}

document.getElementById('convertTable').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-delete-convert]');
  if (!btn) return;
  deleteConvertKeys([btn.dataset.deleteConvert]);
});
document.getElementById('btnDeleteConvert').addEventListener('click', () => {
  deleteConvertKeys([...selectedConvertKeys]);
});


loadCollectSchools().then(async () => {
  try {
    const saved = await fetch('/api/quy-doi/records');
    if (!saved.ok) return;
    const data = await saved.json();
    if (data.ok) renderConversions(data.rows || [], data.download_url || '');
  } catch (_) {}
});
