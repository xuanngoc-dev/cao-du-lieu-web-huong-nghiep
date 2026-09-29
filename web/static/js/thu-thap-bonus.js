var PRIORITY_REGIONS = {
  KV1: { label: 'KV1', points: 75, hint: 'Xã vùng đồng bào dân tộc thiểu số và miền núi, xã khu vực I–III, thôn đặc biệt khó khăn, hải đảo, đặc khu hoặc biên giới. Mức 0,75 điểm.' },
  'KV2-NT': { label: 'KV2-NT', points: 50, hint: 'Địa phương không thuộc KV1, KV2, KV3. Mức 0,50 điểm.' },
  KV2: { label: 'KV2', points: 25, hint: 'Phường thuộc tỉnh và xã của thành phố trực thuộc trung ương, trừ xã thuộc KV1. Mức 0,25 điểm.' },
  KV3: { label: 'KV3', points: 0, hint: 'Phường của thành phố trực thuộc trung ương. Mức 0 điểm.' },
};
var PRIORITY_OBJECTS = {
  '01': { label: 'Đối tượng 01', group: 'UT1', points: 200, hint: 'Người dân tộc thiểu số thuộc diện ưu tiên khu vực 1. Nhóm UT1, mức 2,00 điểm.' },
  '02': { label: 'Đối tượng 02', group: 'UT1', points: 200, hint: 'Thương binh, bệnh binh, người có giấy chứng nhận như thương binh, hoặc quân nhân, sĩ quan, hạ sĩ quan, chiến sĩ nghĩa vụ trong Công an nhân dân đã xuất ngũ. Nhóm UT1, mức 2,00 điểm.' },
  '03': { label: 'Đối tượng 03', group: 'UT1', points: 200, hint: 'Thân nhân liệt sĩ, con thương binh, con bệnh binh hoặc con người hoạt động kháng chiến bị nhiễm chất độc hoá học, suy giảm khả năng lao động từ 81% trở lên. Nhóm UT1, mức 2,00 điểm.' },
  '04': { label: 'Đối tượng 04', group: 'UT2', points: 100, hint: 'Thanh niên xung phong tập trung được cử đi học, hoặc quân nhân Công an nhân dân tại ngũ được cử đi học với thời gian phục vụ trên 15 tháng. Nhóm UT2, mức 1,00 điểm.' },
  '05': { label: 'Đối tượng 05', group: 'UT2', points: 100, hint: 'Người dân tộc thiểu số học ngoài khu vực của đối tượng 01, hoặc con thương binh, con bệnh binh, con người bị nhiễm chất độc hoá học với mức suy giảm dưới 81%. Nhóm UT2, mức 1,00 điểm.' },
  '06': { label: 'Đối tượng 06', group: 'UT2', points: 100, hint: 'Người khuyết tật nặng, giáo viên đã dạy đủ 3 năm dự tuyển ngành sư phạm, hoặc nhân viên y tế, trung cấp Dược đã công tác đủ 3 năm dự tuyển đúng ngành sức khỏe. Nhóm UT2, mức 1,00 điểm.' },
};

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function formatHundredths(value) {
  const sign = value < 0 ? '-' : '';
  const abs = Math.abs(value);
  const whole = Math.floor(abs / 100);
  const frac = String(abs % 100).padStart(2, '0');
  return sign + whole + ',' + frac;
}

function parseHundredths(text) {
  const raw = String(text || '').trim().replace(/\s/g, '').replace(',', '.');
  if (!raw) return null;
  if (!/^\d+(\.\d+)?$/.test(raw)) return NaN;
  const parts = raw.split('.');
  const whole = Number(parts[0]);
  const frac = parts[1] || '';
  if (frac.length <= 2) return whole * 100 + Number((frac + '00').slice(0, 2));
  let hundredths = whole * 100 + Number(frac.slice(0, 2));
  const rest = frac.slice(2);
  if (rest[0] > '4') hundredths += 1;
  return hundredths;
}

function priorityBonusHundredths(score, level) {
  if (score < 2250) return level;
  return Math.floor(((3000 - score) * level + 375) / 750);
}

function syncPriorityHints() {
  const region = PRIORITY_REGIONS[document.getElementById('priorityRegion').value] || null;
  const object = PRIORITY_OBJECTS[document.getElementById('priorityObject').value] || null;
  document.getElementById('priorityRegionHint').textContent = region
    ? region.hint
    : 'Chọn khu vực theo nơi học THPT lâu nhất.';
  document.getElementById('priorityObjectHint').textContent = object
    ? object.hint
    : 'Nếu thuộc nhiều đối tượng, chỉ chọn mức cao nhất.';
}

function renderPriorityCalc() {
  const region = PRIORITY_REGIONS[document.getElementById('priorityRegion').value] || null;
  const object = PRIORITY_OBJECTS[document.getElementById('priorityObject').value] || null;
  const score = parseHundredths(document.getElementById('priorityScore').value);
  const msg = document.getElementById('priorityCalcMsg');
  const result = document.getElementById('priorityCalcResult');
  syncPriorityHints();
  if (score == null) {
    msg.innerHTML = '<div class="alert alert-warning py-2 mb-0">Hãy nhập tổng điểm thi THPT.</div>';
    result.innerHTML = '';
    return;
  }
  if (Number.isNaN(score) || score > 3000) {
    msg.innerHTML = '<div class="alert alert-warning py-2 mb-0">Tổng điểm thi THPT là số từ 0 đến 30.</div>';
    result.innerHTML = '';
    return;
  }
  const regionPoints = region ? region.points : 0;
  const objectPoints = object ? object.points : 0;
  const level = regionPoints + objectPoints;
  const bonus = priorityBonusHundredths(score, level);
  const total = score + bonus;
  const reduced = score >= 2250;
  const parts = [];
  if (region) parts.push(`${region.label} ${formatHundredths(regionPoints)}`);
  if (object) parts.push(`${object.label} ${formatHundredths(objectPoints)}`);
  const levelLine = parts.length ? parts.join(' + ') : 'Không có khu vực hoặc đối tượng ưu tiên';
  const formula = reduced
    ? `[(30 − ${formatHundredths(score)}) / 7,50] × ${formatHundredths(level)}`
    : `${formatHundredths(regionPoints)} + ${formatHundredths(objectPoints)}`;
  msg.innerHTML = '';
  result.innerHTML = `
    <div class="row g-2">
      <div class="col-md-4">
        <div class="calc-result-tile">
          <div class="small text-muted">Mức điểm ưu tiên</div>
          <div class="calc-est">${formatHundredths(level)}</div>
          <div class="small">${escapeHtml(levelLine)}</div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="calc-result-tile is-source">
          <div class="small text-muted">Điểm ưu tiên được cộng</div>
          <div class="calc-est">${formatHundredths(bonus)}</div>
          <div class="small">${escapeHtml(formula)}</div>
        </div>
      </div>
      <div class="col-md-4">
        <div class="calc-result-tile">
          <div class="small text-muted">Tổng điểm sau khi cộng</div>
          <div class="calc-est">${formatHundredths(total)}</div>
          <div class="small">${formatHundredths(score)} + ${formatHundredths(bonus)}</div>
        </div>
      </div>
    </div>
    <p class="small text-muted mt-2 mb-0">${reduced
      ? 'Tổng điểm từ 22,50 trở lên nên điểm ưu tiên được điều chỉnh và làm tròn đến hàng phần trăm.'
      : 'Tổng điểm dưới 22,50 nên cộng đủ mức điểm ưu tiên.'}</p>`;
}

document.getElementById('btnPriorityCalc').addEventListener('click', renderPriorityCalc);
document.getElementById('priorityScore').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') renderPriorityCalc();
});
document.getElementById('priorityScore').addEventListener('input', () => {
  if (document.getElementById('priorityCalcResult').innerHTML) renderPriorityCalc();
});
['priorityRegion', 'priorityObject'].forEach(id => {
  document.getElementById(id).addEventListener('change', () => {
    syncPriorityHints();
    if (document.getElementById('priorityCalcResult').innerHTML) renderPriorityCalc();
  });
});
