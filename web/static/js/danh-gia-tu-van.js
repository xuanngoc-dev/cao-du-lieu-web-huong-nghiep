(function () {
  const regionEl = document.getElementById('tvRegion');
  const sectorEl = document.getElementById('tvSector');
  const schoolEl = document.getElementById('tvSchool');
  const comboEl = document.getElementById('tvCombo');
  const scoreBox = document.getElementById('tvScores');
  const logEl = document.getElementById('tvLog');
  const form = document.getElementById('tvForm');
  if (!regionEl || !form) return;

  let catalog = null;
  let source = 'thi';
  let busy = false;

  const SUGGESTIONS = [
    'Với điểm thi THPT khối A00 của tôi thì có thể dự tuyển vào ngành nào của khối kỹ thuật?',
    'Tổ hợp D01 của tôi phù hợp những ngành kinh tế nào ở miền Bắc?',
    'Điểm A01 của tôi so với điểm chuẩn các ngành công nghệ ở Bách khoa Hà Nội thế nào?',
    'Thống kê điểm chuẩn các ngành công nghệ ở Bách khoa Hà Nội cho tôi?',
  ];

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  }

  function fmt(value) {
    if (value == null || value === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return String(Math.round(number * 100) / 100).replace('.', ',');
  }

  function values(el) {
    return window.App.getSelectValues(el);
  }

  function fillSelect(el, html, selected) {
    window.App.setSelectOptions(el, html, selected || [], {
      placeholder: el.getAttribute('data-placeholder') || 'Chọn…',
    });
  }

  function schoolPool() {
    const regions = values(regionEl);
    const sectors = values(sectorEl);
    return (catalog.schools || []).filter((school) => {
      if (sectors.length && !sectors.includes(school.loai_truong || '')) return false;
      if (regions.length) {
        const area = String(school.khu_vuc || '');
        if (!regions.some((region) => area.includes(region))) return false;
      }
      return true;
    });
  }

  function renderSchools(keep) {
    const selected = keep || values(schoolEl);
    const rows = schoolPool();
    const html = rows.map((school) => (
      `<option value="${esc(school.code)}">${esc(school.name)} [${esc(school.code)}]</option>`
    )).join('');
    const still = selected.filter((code) => rows.some((school) => school.code === code));
    fillSelect(schoolEl, html, still);
  }

  function readScores() {
    const scores = {};
    scoreBox.querySelectorAll('[data-score-key]').forEach((input) => {
      const raw = input.value.trim().replace(',', '.');
      if (raw !== '') scores[input.dataset.scoreKey] = raw;
    });
    return scores;
  }

  function scoreMap() {
    if (!catalog) return {};
    return source === 'hoc_ba' ? (catalog.hoc_ba || {}) : (catalog.scores || {});
  }

  function renderScores() {
    const saved = scoreMap();
    scoreBox.innerHTML = (catalog.subjects || []).map((subject) => {
      const value = saved[subject.key] != null ? saved[subject.key] : '';
      return `
        <div class="col-6 col-md-4 col-xl-2">
          <label class="form-label small mb-1" for="tvScore-${esc(subject.key)}">${esc(subject.label)}</label>
          <input class="form-control form-control-sm" id="tvScore-${esc(subject.key)}"
            data-score-key="${esc(subject.key)}" type="number" min="0" max="10" step="0.01"
            value="${esc(value)}" inputmode="decimal">
        </div>`;
    }).join('');
    const hint = document.getElementById('tvScoreHint');
    const name = catalog.profile && catalog.profile.ho_ten ? ` của ${catalog.profile.ho_ten}` : '';
    hint.textContent = source === 'hoc_ba'
      ? `Điểm học bạ trung bình các năm${name}. Sửa tại đây không ghi đè hồ sơ.`
      : `Điểm thi THPT${name}, lấy từ điểm thi thử trong hồ sơ nếu chưa có điểm chính thức.`;
  }

  function renderPreview() {
    const box = document.getElementById('tvComboPreview');
    const selected = values(comboEl);
    const scores = readScores();
    if (!selected.length) {
      box.textContent = '';
      return;
    }
    const bits = selected.map((code) => {
      const combo = (catalog.combos || []).find((item) => item.code === code);
      if (!combo) return '';
      const missing = (combo.keys || []).filter((key) => !key || scores[key] == null || scores[key] === '');
      if (missing.length) return `${code}: thiếu môn`;
      const total = (combo.keys || []).reduce((sum, key) => sum + Number(scores[key] || 0), 0);
      return `${code}: ${fmt(total)}`;
    }).filter(Boolean);
    box.textContent = bits.length ? `Tổng điểm tổ hợp (chưa cộng ưu tiên): ${bits.join(' · ')}` : '';
  }

  function badge(status) {
    const tone = {
      dat: 'text-bg-success',
      sat: 'text-bg-warning',
      chua: 'text-bg-secondary',
      thieu: 'text-bg-light border',
      thang: 'text-bg-info',
    }[status] || 'text-bg-light';
    return tone;
  }

  function schoolTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)}</td>
        <td>${esc(row.ma_truong)}</td>
        <td>${esc(row.khu_vuc || '—')}</td>
        <td>${esc(row.loai_truong || '—')}</td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr><th>Trường</th><th>Mã</th><th>Khu vực</th><th>Loại hình</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function methodSchoolTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)} <span class="text-muted">${esc(row.ma_truong)}</span></td>
        <td>${esc(row.khu_vuc || '—')}</td>
        <td>${esc(row.loai_truong || '—')}</td>
        <td>${esc(row.phuong_thuc || '—')}</td>
        <td class="text-end">${row.tong_nganh ? `${esc(row.so_nganh)}/${esc(row.tong_nganh)}` : esc(row.so_nganh)}</td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr><th>Trường</th><th>Khu vực</th><th>Loại hình</th><th>Phương thức</th><th class="text-end">Số ngành</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function convertTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.phuong_thuc)}${row.vai_tro ? ` <span class="text-muted">${esc(row.vai_tro)}</span>` : ''}</td>
        <td class="text-end">${esc(row.diem)}</td>
        <td>${esc(row.khoang || '—')}</td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr><th>Phương thức</th><th class="text-end">Điểm</th><th>Khoảng phân vị</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function catalogTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)} <span class="text-muted">${esc(row.ma_truong)}</span></td>
        <td>${esc(row.ten)}</td>
        <td>${esc(row.ma_chuan || '—')}</td>
        <td class="text-end">${esc(row.so_nganh)}</td>
        <td>${esc(row.to_hop || '—')}</td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr><th>Trường</th><th>Phương thức</th><th>Mã chuẩn</th><th class="text-end">Số ngành</th><th>Tổ hợp</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function methodTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)} <span class="text-muted">${esc(row.ma_truong)}</span></td>
        <td>${esc(row.ten_nganh)}${row.ma_xet_tuyen ? ` <span class="text-muted">${esc(row.ma_xet_tuyen)}</span>` : ''}</td>
        <td>${esc((row.phuong_thuc || []).join(', ') || '—')}</td>
        <td class="text-end">${fmt(row.diem_chuan)}${row.nam ? ` <span class="text-muted">${esc(row.nam)}</span>` : ''}</td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr><th>Trường</th><th>Ngành</th><th>Phương thức</th><th class="text-end">Điểm chuẩn</th></tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function certTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)} <span class="text-muted">${esc(row.ma_truong)}</span></td>
        <td>${esc(row.loai_truong || '—')}</td>
        <td class="text-end">${fmt(row.diem_cua_toi)}</td>
        <td class="text-end">${fmt(row.diem_thap)}</td>
        <td class="text-end">${fmt(row.diem_cao)}</td>
        <td class="text-end">${row.tong_nganh ? `${row.du_nganh}/${row.tong_nganh}` : '—'}</td>
        <td><span class="badge ${badge(row.trang_thai)}">${esc(row.trang_thai_nhan)}</span></td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr>
              <th>Trường</th><th>Loại hình</th><th class="text-end">Điểm của tôi</th>
              <th class="text-end">Chuẩn thấp nhất</th><th class="text-end">Chuẩn cao nhất</th>
              <th class="text-end">Ngành đủ điểm</th><th>Kết luận</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function statsTable(rows, methods) {
    if (!rows || !rows.length) return '';
    const cols = methods && methods.length
      ? methods
      : [...new Set(rows.flatMap((row) => Object.keys(row.diem || {})))];
    const head = cols.map((name) => `<th class="text-end">${esc(name)}</th>`).join('');
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)} <span class="text-muted">${esc(row.ma_truong)}</span></td>
        <td>${esc(row.ten_nganh)}${row.ma_xet_tuyen ? ` <span class="text-muted">${esc(row.ma_xet_tuyen)}</span>` : ''}</td>
        ${cols.map((name) => `<td class="text-end">${fmt((row.diem || {})[name])}</td>`).join('')}
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr><th>Trường</th><th>Ngành</th>${head}</tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function rowTable(rows) {
    if (!rows || !rows.length) return '';
    const body = rows.map((row) => `
      <tr>
        <td>${esc(row.ten_truong)} <span class="text-muted">${esc(row.ma_truong)}</span></td>
        <td>${esc(row.ten_nganh)}${row.ma_xet_tuyen ? ` <span class="text-muted">${esc(row.ma_xet_tuyen)}</span>` : ''}</td>
        <td>${esc(row.to_hop)}</td>
        <td>${esc((row.phuong_thuc || []).join('; ') || '—')}</td>
        <td class="text-end">${fmt(row.diem_chuan)}${row.nam ? ` <span class="text-muted">${esc(row.nam)}</span>` : ''}</td>
        <td class="text-end">${row.chenh == null ? '—' : fmt(row.chenh)}</td>
        <td><span class="badge ${badge(row.trang_thai)}">${esc(row.trang_thai_nhan)}</span></td>
      </tr>`).join('');
    return `
      <div class="table-responsive mt-2">
        <table class="table table-sm table-bordered align-middle mb-0 bg-white">
          <thead class="table-light">
            <tr>
              <th>Trường</th><th>Ngành</th><th>Tổ hợp</th><th>Phương thức tuyển sinh</th>
              <th class="text-end">Điểm chuẩn THPT</th><th class="text-end">Chênh</th><th>Kết luận</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function addMessage(role, html) {
    const node = document.createElement('div');
    node.className = `tu-van-msg ${role}`;
    node.innerHTML = html;
    logEl.appendChild(node);
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function ask(question) {
    const text = question.trim();
    if (!text || busy) return;
    busy = true;
    document.getElementById('tvSend').disabled = true;
    addMessage('user', esc(text));
    addMessage('bot', 'Đang trả lời…');
    const pending = logEl.lastElementChild;
    try {
      const res = await fetch('/api/danh-gia/hoi', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: text,
          regions: values(regionEl),
          sectors: values(sectorEl),
          schools: values(schoolEl),
          combos: values(comboEl),
          scores: readScores(),
          score_source: source,
        }),
      });
      const raw = await res.text();
      let data;
      try {
        data = JSON.parse(raw);
      } catch (parseError) {
        throw new Error(res.status === 404
          ? 'Máy chủ đang chạy bản cũ, chưa có API tư vấn. Hãy tắt ứng dụng rồi chạy lại.'
          : 'Máy chủ trả về lỗi thay vì dữ liệu JSON.');
      }
      if (!data.ok && data.error) throw new Error(data.error);
      const notes = (data.notes || []).map((note) => `<div class="small text-muted mt-1">${esc(note)}</div>`).join('');
      const table = data.kind === 'schools'
        ? schoolTable(data.rows || [])
        : data.kind === 'stats'
          ? statsTable(data.rows || [], data.methods || [])
          : data.kind === 'certs'
            ? certTable(data.rows || [])
            : data.kind === 'methods'
              ? methodTable(data.rows || [])
              : data.kind === 'catalog'
                ? catalogTable(data.rows || [])
              : data.kind === 'methodSchools'
                ? methodSchoolTable(data.rows || [])
              : data.kind === 'convert'
                ? convertTable(data.rows || [])
                : rowTable(data.rows || []);
      pending.innerHTML = `<div>${esc(data.summary || '')}</div>${notes}${table}`;
    } catch (error) {
      pending.innerHTML = esc(error.message || 'Không trả lời được.');
    } finally {
      busy = false;
      document.getElementById('tvSend').disabled = false;
      logEl.scrollTop = logEl.scrollHeight;
    }
  }

  function bindSource(id, next) {
    document.getElementById(id).addEventListener('click', () => {
      source = next;
      document.getElementById('tvSrcThi').className = `btn btn-sm ${source === 'thi' ? 'btn-accent' : 'btn-outline-teal'}`;
      document.getElementById('tvSrcHb').className = `btn btn-sm ${source === 'hoc_ba' ? 'btn-accent' : 'btn-outline-teal'}`;
      renderScores();
      renderPreview();
    });
  }

  document.getElementById('tvSuggest').innerHTML = SUGGESTIONS.map((text) => (
    `<button type="button" class="btn btn-outline-teal btn-sm" data-ask="${esc(text)}">${esc(text)}</button>`
  )).join('');
  document.getElementById('tvSuggest').addEventListener('click', (event) => {
    const button = event.target.closest('[data-ask]');
    if (!button) return;
    document.getElementById('tvInput').value = button.dataset.ask;
    ask(button.dataset.ask);
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const input = document.getElementById('tvInput');
    const text = input.value;
    input.value = '';
    ask(text);
  });
  scoreBox.addEventListener('input', renderPreview);
  regionEl.addEventListener('change', () => renderSchools());
  sectorEl.addEventListener('change', () => renderSchools());
  comboEl.addEventListener('change', renderPreview);
  document.getElementById('tvReloadScores').addEventListener('click', () => {
    renderScores();
    renderPreview();
  });
  bindSource('tvSrcThi', 'thi');
  bindSource('tvSrcHb', 'hoc_ba');

  fetch('/api/danh-gia/tieu-chi')
    .then((res) => res.json())
    .then((data) => {
      if (!data.ok) return;
      catalog = data;
      fillSelect(regionEl, data.regions.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join(''));
      fillSelect(sectorEl, data.sectors.map((item) => (
        `<option value="${esc(item.id)}">${esc(item.id)}</option>`
      )).join(''));
      fillSelect(comboEl, data.combos.map((item) => (
        `<option value="${esc(item.code)}">${esc(item.code)} — ${esc(item.subjects)}</option>`
      )).join(''));
      renderSchools([]);
      renderScores();
      renderPreview();
    })
    .catch(() => {
      addMessage('bot', 'Chưa tải được danh sách tiêu chí.');
    });
})();
