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
  const all = series.flatMap(s => s.points.map(p => p.v)).filter(v => v != null);
  if (!all.length) { el.innerHTML = '<div class="empty">暂无数据（未接入或未采集）</div>'; return; }
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1; min -= pad; max += pad;
  const n = Math.max(2, ...series.map(s => s.points.length));
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
    g += `<polyline fill="none" stroke="${s.color}" stroke-width="2"
           ${s.dash ? 'stroke-dasharray="7 5"' : ''} points="${pts.join(' ')}"/>`;
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

/* ---------------- 仪表盘（半环仪表 + 安全区着色） ----------------
   gauge(el, {label, value, unit, min, max, ok:[lo,hi]})
   值超出 [min,max] 时指针钉在边界并标红；ok 区间为绿色安全区。 */
function gauge(el, opt) {
  const W = 210, H = 132, cx = W / 2, cy = 104, R = 80;
  const min = opt.min ?? 0, max = opt.max ?? 100;
  const hasVal = opt.value != null && isFinite(opt.value);
  const v = hasVal ? Math.min(Math.max(opt.value, min), max) : min;
  const frac = max > min ? (v - min) / (max - min) : 0;
  const out = hasVal && (opt.value < min || opt.value > max);

  // SVG 的 y 轴向下：取 -sin 让半环画在上方；比例 0=左端，1=右端
  const pol = deg => [cx + R * Math.cos(deg * Math.PI / 180),
                      cy - R * Math.sin(deg * Math.PI / 180)];
  const deg = f => 180 - f * 180;   // 比例 f → 角度
  // 用折线采样画弧：比 SVG arc 标志位更直观可靠
  const arc = (f0, f1, color, width) => {
    if (f1 <= f0) return '';
    const pts = [];
    const nSeg = Math.max(6, Math.ceil((f1 - f0) * 36));
    for (let k = 0; k <= nSeg; k++) {
      const [x, y] = pol(deg(f0 + (f1 - f0) * k / nSeg));
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return `<polyline points="${pts.join(' ')}" fill="none" stroke="${color}"
             stroke-width="${width}" stroke-linecap="round"/>`;
  };
  // 底环 + 安全区（默认取量程中段 25%~75%）
  const okLo = opt.ok ? Math.min(Math.max((opt.ok[0] - min) / (max - min), 0), 1) : 0.25;
  const okHi = opt.ok ? Math.min(Math.max((opt.ok[1] - min) / (max - min), 0), 1) : 0.75;
  let svg = `<svg viewBox="0 0 ${W} ${H}" class="gauge">
    ${arc(0, 1, '#e8edf2', 12)}
    ${arc(okLo, okHi, '#7fb069', 12)}
    ${hasVal ? arc(0, frac, out ? '#d94f3d' : '#2e75b6', 12) : ''}`;
  if (hasVal) {
    const [nx, ny] = pol(deg(frac));
    const [bx, by] = pol(deg(frac) + 180);
    svg += `<line x1="${(cx + (bx - cx) * 0.14).toFixed(1)}" y1="${(cy - (cy - by) * 0.14).toFixed(1)}"
              x2="${(cx + (nx - cx) * 0.62).toFixed(1)}" y2="${(cy - (cy - ny) * 0.62).toFixed(1)}"
              stroke="${out ? '#d94f3d' : '#22303c'}" stroke-width="2.5" stroke-linecap="round"/>
            <circle cx="${cx}" cy="${cy}" r="4" fill="#22303c"/>`;
  }
  svg += `<text x="10" y="${H - 4}" class="axis">${min}${opt.unit || ''}</text>
          <text x="${W - 10}" y="${H - 4}" class="axis" text-anchor="end">${max}${opt.unit || ''}</text>
          <text x="${cx}" y="${cy - 40}" text-anchor="middle" class="gauge-v"
                fill="${out ? '#d94f3d' : '#22303c'}">
            ${hasVal ? opt.value : '未接入'}</text>
          <text x="${cx}" y="${cy - 26}" text-anchor="middle" class="axis">${hasVal ? (opt.unit || '') : ''}</text>
        </svg>`;
  el.innerHTML = `<div class="k">${esc(opt.label || '')}${out ? ' <span class="pill bad">超范围</span>' : ''}</div>${svg}`;
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
             background:${x.color || 'linear-gradient(90deg,#2e75b6,#548235)'}"></div>
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
        ? ' · <b>终端仿真已开启</b>：投喂任务会自动应答并跑完闭环。'
        : ' · <b>终端仿真已关闭</b>：下发后收不到回执，可复现「结果未知（待核查）」异常流程。';
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
