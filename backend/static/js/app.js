/* 白蕉水产养殖管理平台 · 前端公共逻辑（无外部依赖，离线可用） */

const API = {
  async req(method, path, body) {
    const user = localStorage.getItem('bj_user') || 'owner';
    const opt = { method, headers: { 'X-User': user, 'Content-Type': 'application/json' } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    const r = await fetch(path, opt);
    let data = {};
    try { data = await r.json(); } catch (e) { data = { ok: false, message: '响应解析失败' }; }
    return { status: r.status, ...data };
  },
  get(p) { return this.req('GET', p); },
  post(p, b) { return this.req('POST', p, b); },
  put(p, b) { return this.req('PUT', p, b); },
};

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function toast(msg, kind = 'info') {
  let box = $('#toast');
  if (!box) {
    box = document.createElement('div'); box.id = 'toast';
    document.body.appendChild(box);
  }
  const d = document.createElement('div');
  d.className = 'toast ' + kind;
  d.textContent = msg;
  box.appendChild(d);
  setTimeout(() => d.remove(), 3200);
}

/* ---------------- 极简 SVG 折线图（无外部依赖） ---------------- */
function lineChart(el, series, opts = {}) {
  const W = opts.width || el.clientWidth || 720;
  const H = opts.height || 220;
  const P = { l: 46, r: 12, t: 14, b: 26 };
  const all = series.flatMap(s => s.points.map(p => p.v)).filter(v => v != null);
  if (!all.length) { el.innerHTML = '<div class="empty">暂无数据（未接入或未采集）</div>'; return; }
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1; min -= pad; max += pad;
  const n = Math.max(series[0].points.length, 2);
  const X = i => P.l + (W - P.l - P.r) * (n <= 1 ? 0 : i / (n - 1));
  const Y = v => P.t + (H - P.t - P.b) * (1 - (v - min) / (max - min));

  let g = `<svg viewBox="0 0 ${W} ${H}" class="chart" preserveAspectRatio="none">`;
  // 网格与 Y 轴刻度
  for (let k = 0; k <= 4; k++) {
    const y = P.t + (H - P.t - P.b) * k / 4;
    const val = (max - (max - min) * k / 4).toFixed(1);
    g += `<line x1="${P.l}" y1="${y}" x2="${W - P.r}" y2="${y}" stroke="#eef1f4"/>`;
    g += `<text x="${P.l - 6}" y="${y + 4}" text-anchor="end" class="axis">${val}</text>`;
  }
  series.forEach(s => {
    const pts = s.points.map((p, i) => p.v == null ? null : `${X(i)},${Y(p.v)}`).filter(Boolean);
    if (!pts.length) return;
    g += `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${pts.join(' ')}"/>`;
    // 无效点单独标记
    s.points.forEach((p, i) => {
      if (p.v == null) return;
      const bad = p.valid === false;
      g += `<circle cx="${X(i)}" cy="${Y(p.v)}" r="${bad ? 4 : 2.4}" fill="${bad ? '#e04b4b' : s.color}"
             ${bad ? 'stroke="#fff" stroke-width="1"' : ''}><title>${esc(p.label || '')} ${p.v}${s.unit || ''}${bad ? '（无效）' : ''}</title></circle>`;
    });
  });
  g += '</svg>';
  el.innerHTML = g + (opts.legend === false ? '' :
    `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}${s.unit ? '（' + s.unit + '）' : ''}</span>`).join('')}
     <span class="hint">红点=无效值（已排除在建议输入外）</span></div>`);
}

/* ---------------- 用户切换 ---------------- */
async function initUserSelect() {
  const sel = $('#userSelect');
  if (!sel) return;
  const r = await API.get('/api/users');
  if (!r.ok) return;
  const cur = localStorage.getItem('bj_user') || 'owner';
  sel.innerHTML = r.data.map(u =>
    `<option value="${esc(u.username)}" ${u.username === cur ? 'selected' : ''}>
       ${esc(u.display_name)}（${roleCN(u.role)}）</option>`).join('');
  if (!r.data.some(u => u.username === cur)) localStorage.setItem('bj_user', 'owner');
  sel.onchange = () => { localStorage.setItem('bj_user', sel.value); location.reload(); };
}

function roleCN(r) { return { owner: '塘主', admin: '管理员', operator: '运营者' }[r] || r; }

function statusCN(s) {
  return {
    pending: '待确认', confirmed: '已确认', cancelled: '已取消', expired: '已失效',
    dispatched: '已下发待回执', running: '执行中', done: '已完成', stopped: '已停止',
    failed: '失败', unknown: '结果未知（待核查）',
    open: '未处理', processing: '处理中', closed: '已关闭', handled: '已处理',
    false_alarm: '误报', online: '在线', offline: '离线',
  }[s] || s;
}

function fmtTime(s) { return s ? s.slice(5, 16) : '—'; }

document.addEventListener('DOMContentLoaded', initUserSelect);
