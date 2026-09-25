var bonusRows = [];
var selectedBonusKeys = new Set();
function bonusKey(row) {
  return [row.ma_truong, row.nam, row.phuong_thuc, row.dieu_kien, row.diem_cong].join('\u001f');
}
function renderBonus(rows) {
  bonusRows = rows || [];
  const alive = new Set(bonusRows.map(bonusKey));
  [...selectedBonusKeys].forEach(key => {
    if (!alive.has(key)) selectedBonusKeys.delete(key);
  });
  document.querySelector('#bonusTable tbody').innerHTML = bonusRows.map((row, i) => {
    const key = bonusKey(row);
    const checked = selectedBonusKeys.has(key) ? 'checked' : '';
    return `
    <tr>
      <td class="text-center"><input class="form-check-input bonus-pick" type="checkbox" data-key="${escapeHtml(key)}" aria-label="Chọn điểm cộng ${i + 1}" ${checked}></td>
      <td class="text-muted">${i + 1}</td>
      <td class="text-nowrap">${escapeHtml(codeName(row.ma_truong, row.ten_truong))}</td>
      <td class="text-center">${escapeHtml(row.nam || '—')}</td>
      <td>${escapeHtml(row.phuong_thuc || '—')}</td>
      <td class="convert-body">${escapeHtml(row.dieu_kien || '—')}</td>
      <td class="text-nowrap">${escapeHtml(row.diem_cong || '—')}</td>
      <td class="text-nowrap">
        ${sourceUrls([row.nguon]).length
          ? `<button type="button" class="btn btn-outline-secondary btn-sm" data-bonus-source="${i}">Chi tiết</button>`
          : '—'}
      </td>
      <td>
        <button type="button" class="btn btn-outline-danger btn-sm" data-delete-bonus="${escapeHtml(key)}" title="Xoá dòng này">
          <i class="bi bi-trash3"></i>
        </button>
      </td>
    </tr>`;
  }).join('');
  document.getElementById('bonusInfo').textContent = bonusRows.length
    ? `${bonusRows.length} mức điểm cộng`
    : 'Không có điểm cộng cho trường đã chọn';
  updateBonusSelect();
}

async function loadBonus(codes, years) {
  const params = new URLSearchParams();
  if (codes && codes.length) params.set('schools', codes.join(','));
  if (years && years.length) params.set('years', years.join(','));
  const res = await fetch('/api/crawl/bonus?' + params.toString());
  const data = await res.json();
  if (!data.ok) throw new Error(data.error || 'Không đọc được điểm cộng');
  renderBonus(data.records || []);
  return data.records || [];
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
  setProgress('run', `Đang thu thập điểm cộng 0/${codes.length} trường…`, 0);
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
          setProgress('run', event.log || `${event.code}: ${event.index}/${event.total}`, pct);
        } else if (event.type === 'finalize') {
          setProgress('run', event.message || 'Đang lọc điểm cộng…', 95);
        } else if (event.type === 'error') {
          throw new Error(event.error || 'Thu thập điểm cộng thất bại');
        }
      }
    }
    const rows = await loadBonus(codes, years);
    setProgress('done', 'Thu thập điểm cộng hoàn tất', 100);
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Thu thập thành công <b>${rows.length}</b> mức điểm cộng theo phương thức và điều kiện.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
    setProgress('hide');
  } finally {
    setCollectBusy(false);
  }
});

document.getElementById('bonusTable').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-bonus-source]');
  if (!btn) return;
  const row = bonusRows[Number(btn.dataset.bonusSource)];
  if (!row) return;
  document.getElementById('resultSourceCaption').textContent = [
    codeName(row.ma_truong, row.ten_truong),
    row.phuong_thuc || '',
    row.dieu_kien || '',
  ].filter(Boolean).join(' · ');
  const urls = sourceUrls([row.nguon]);
  document.getElementById('resultSourceList').innerHTML = urls.length
    ? urls.map(url => `<li class="mb-1"><a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a></li>`).join('')
    : '<li>Không có liên kết.</li>';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('resultSourceModal')).show();
});

function updateBonusSelect() {
  updatePickSelect('.bonus-pick', 'bonusCheckAll', 'btnDeleteBonus', selectedBonusKeys);
}
bindPickTable('bonusTable', '.bonus-pick', 'bonusCheckAll', selectedBonusKeys, updateBonusSelect);
async function deleteBonusKeys(keys) {
  const unique = [...new Set((keys || []).filter(Boolean))];
  if (!unique.length) return;
  const label = unique.length === 1 ? 'dòng điểm cộng đã chọn' : `${unique.length} dòng điểm cộng đã chọn`;
  const accepted = await confirmDeleteResults(`Xoá ${label} khỏi dữ liệu đã tải?`);
  if (!accepted) return;
  const payload = unique.map(key => bonusRows.find(row => bonusKey(row) === key)).filter(Boolean);
  const msg = document.getElementById('collectMsg');
  try {
    const res = await fetch('/api/crawl/bonus/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ keys: payload }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Không xoá được');
    unique.forEach(key => selectedBonusKeys.delete(key));
    const codes = window.App.getSelectValues(document.getElementById('schoolPick'));
    await loadBonus(codes, selectedYears());
    msg.innerHTML = `<div class="alert alert-success py-2 mb-0">Đã xoá ${unique.length} dòng điểm cộng.</div>`;
  } catch (e) {
    msg.innerHTML = `<div class="alert alert-danger py-2 mb-0">${escapeHtml(e.message)}</div>`;
  }
}

document.getElementById('bonusTable').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-delete-bonus]');
  if (!btn) return;
  deleteBonusKeys([btn.dataset.deleteBonus]);
});

document.getElementById('btnDeleteBonus').addEventListener('click', () => {
  deleteBonusKeys([...selectedBonusKeys]);
});


loadCollectSchools().then(async () => {
  try {
    await loadBonus([], []);
  } catch (_) {}
});
