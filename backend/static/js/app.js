/* 白蕉水产养殖管理平台 · 前端公共逻辑（无外部依赖，离线可用） */

const API = {
  async req(method, path, body) {
    const user = localStorage.getItem('bj_user') || 'owner';
    const opt = { method, headers: { 'X-User': user, 'Content-Type': 'application/json' } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    let r;
    try {
      r = await fetch(path, opt);
    } catch (e) {
      // 断网/后端未启动：返回统一错误结构，调用方据此恢复按钮状态
      return { status: 0, ok: false, message: '网络异常：无法连接后端，请检查网络后重试' };
    }
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
  const all = series.flatMap(s => s.points.map(p => p.v)).filter(v => Number.isFinite(v));
  if (!all.length) { el.innerHTML = '<div class="empty">暂无数据（未接入或未采集）</div>'; return; }
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1; min -= pad; max += pad;
  const n = Math.max(2, ...series.map(s => s.points.length));
  const stamps = series.flatMap(s => s.points.map(p => p.t ? Date.parse(p.t.replace(' ', 'T')) : NaN)).filter(Number.isFinite);
  const startT = Math.min(...stamps), endT = Math.max(...stamps);
  const X = (i, p) => P.l + (W - P.l - P.r) * (stamps.length && p?.t && endT > startT ? (Date.parse(p.t.replace(' ', 'T')) - startT) / (endT - startT) : i / (n - 1));
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
    const segments = []; let segment = [];
    s.points.forEach((p, i) => {
      const prev = s.points[i - 1];
      const gap = p.t && prev?.t && Date.parse(p.t.replace(' ', 'T')) - Date.parse(prev.t.replace(' ', 'T')) > 8 * 3600000;
      if (p.v == null || p.valid === false || gap) { if (segment.length) segments.push(segment); segment = []; }
      if (p.v != null && p.valid !== false) segment.push(`${X(i, p)},${Y(p.v)}`);
    });
    if (segment.length) segments.push(segment);
    segments.forEach(pts => { g += `<polyline fill="none" stroke="${s.color}" stroke-width="2" ${s.dash ? 'stroke-dasharray="7 5"' : ''} points="${pts.join(' ')}"/>`; });
    // 无效点单独标记
    s.points.forEach((p, i) => {
      if (p.v == null) return;
      const bad = p.valid === false;
      g += `<circle cx="${X(i, p)}" cy="${Y(p.v)}" r="${bad ? 4 : 2.4}" fill="${bad ? '#e04b4b' : s.color}"
             ${bad ? 'stroke="#fff" stroke-width="1"' : ''}><title>${esc(p.label || '')} ${p.v}${s.unit || ''}${bad ? '（无效）' : ''}</title></circle>`;
    });
  });
  if (stamps.length && endT > startT) {
    for (let i = 0; i < 5; i++) {
      const d = new Date(startT + (endT - startT) * i / 4);
      g += `<text x="${P.l + (W - P.l - P.r) * i / 4}" y="${H - 6}" text-anchor="middle" class="axis">${d.getMonth() + 1}/${d.getDate()}</text>`;
    }
  }
  g += '</svg>';
  el.innerHTML = g + (opts.legend === false ? '' :
    `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}${s.unit ? '（' + s.unit + '）' : ''}</span>`).join('')}
     <span class="hint">无效值单独标记；带时间的测量间隔超过8小时断线</span></div>`);
}

/* ---------------- 仪表盘（半环仪表 + 安全区着色） ----------------
   gauge(el, {label, value, unit, min, max, ok:[lo,hi]})
   值超出 [min,max] 时指针钉在边界并标红；ok 区间为绿色安全区。 */
function gauge(el, opt) {
  const hasValue = opt.value != null && Number.isFinite(Number(opt.value));
  const percentage = hasValue ? Math.max(0, Math.min(100, (opt.value - opt.min) / (opt.max - opt.min) * 100)) : 0;
  el.innerHTML = `<div class="row"><span class="k">${esc(opt.label)}</span><span class="spacer"></span><span class="pill ${opt.expired ? 'warn' : ''}">${hasValue ? sourceCN(opt.source) : '未接入'}</span></div>
    <div class="sensor-reading">${hasValue ? esc(opt.value) : '—'}<small>${esc(opt.unit)}</small></div>
    <div class="sensor-range"><span style="width:${percentage}%"></span></div>
    <div class="sensor-foot"><span>${hasValue ? '量程位置 · 非安全评级' : '等待设备数据'}</span><span>${hasValue ? esc(fmtTime(opt.time)) : ''}</span></div>`;
}

/* ---------------- 环形进度 ----------------
   ring(el, {label, value, max, unit, color}) — value/max 比例画环 */
function ring(el, opt) {
  const S = 132, cx = S / 2, cy = S / 2, R = 48;
  const C = 2 * Math.PI * R;
  const max = opt.max > 0 ? opt.max : 1;
  const frac = Math.min(Math.max((opt.value || 0) / max, 0), 1);
  const color = opt.color || '#2e8b57';
  el.innerHTML = `
    <div class="k">${esc(opt.label || '')}</div>
    <svg viewBox="0 0 ${S} ${S}" class="gauge">
      <circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="#e8edf2" stroke-width="12"/>
      ${frac > 0 ? `<circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="${color}" stroke-width="12"
              stroke-linecap="${frac > 0.02 ? 'round' : 'butt'}" stroke-dasharray="${(C * frac).toFixed(1)} ${C.toFixed(1)}"
              transform="rotate(-90 ${cx} ${cy})"/>` : ''}
      <text x="${cx}" y="${cy + 2}" text-anchor="middle" class="gauge-v">${opt.value ?? 0}</text>
      <text x="${cx}" y="${cy + 18}" text-anchor="middle" class="axis">/ ${max}${opt.unit || ''}</text>
    </svg>
    <div class="s">${esc(opt.note || '')}</div>`;
}

/* ---------------- 横向条形图（多塘对比等） ----------------
   barChart(el, [{name, value, unit, color}]) — 值带单位的横向条 */
function barChart(el, items, opts = {}) {
  const rows = items.filter(x => x.value != null);
  if (!rows.length) { el.innerHTML = '<div class="empty">暂无数据（未接入或未采集）</div>'; return; }
  const maxV = Math.max(...rows.map(x => Math.abs(x.value)), 0.0001);
  el.innerHTML = `<div class="bars">${rows.map(x => `
    <div class="bar-row">
      <span class="bar-name">${esc(x.name)}</span>
      <div class="bar-track">
        <div class="bar-fill" style="width:${(Math.abs(x.value) / maxV * 100).toFixed(1)}%;
             background:${x.color || '#91ab72'}"></div>
      </div>
      <span class="bar-val">${x.value}${esc(x.unit || '')}</span>
    </div>`).join('')}</div>
    ${opts.note ? `<div class="hint" style="margin-top:6px">${esc(opts.note)}</div>` : ''}`;
}

/* ---------------- 用户切换 ---------------- */async function initUserSelect() {
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

/* 本地日期 YYYY-MM-DD（不用 toISOString，避免时区把凌晨算成昨天） */
function localToday() {
  const d = new Date();
  return d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0');
}

document.addEventListener('DOMContentLoaded', initUserSelect);

/* ---------------- 演示开关：终端仿真器 ---------------- */
async function initSimSwitch() {
  const btn = document.getElementById('simToggle');
  if (!btn) return;

  async function refresh() {
    const r = await API.get('/api/simulator/status');
    const on = !!(r.ok && r.data && r.data.running);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.classList.toggle('on', on);
    document.getElementById('simText').textContent = on ? '运行中' : '已关闭';
    const hint = document.getElementById('simHint');
    if (hint) {
      hint.innerHTML = on
        ? '仿真终端运行中，投喂任务将返回模拟执行结果。'
        : '仿真已关闭。下发无回执时，任务将进入待核查。';
    }
    btn.disabled = false;
  }

  btn.disabled = true;
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      // 动作前先取一次真实状态：仿真终端是后端内存线程，
      // 后端重启后会归零，若沿用页面上缓存的状态会发错命令
      // （表现为「点了没反应」）。
      const cur = await API.get('/api/simulator/status');
      const running = !!(cur.ok && cur.data && cur.data.running);
      const r = await API.post(running ? '/api/simulator/stop' : '/api/simulator/start', {});
      if (!r.ok) {
        toast(r.message || '操作失败', 'err');
      } else {
        toast(r.message || (running ? '已关闭仿真终端' : '已开启仿真终端'), 'ok');
      }
    } finally {
      if (window.__simTimer) clearInterval(window.__simTimer);
      await refresh();
    }
  };

  await refresh();
  // 仿真终端由后端线程运行，页面刷新后状态仍需保持同步
  window.__simTimer = setInterval(refresh, 5000);
}

document.addEventListener('DOMContentLoaded', initSimSwitch);
