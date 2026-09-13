/* Read-only overview: every displayed value comes from authorized API responses. */
let pondRows = [],
  metric = "temperature",
  trendRequest = 0;
const nfmt = (v) =>
  Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const pondMark =
  '<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="m4 11 12-6 12 6-12 6-12-6ZM4 16l12 6 12-6M4 21l12 6 12-6"/></svg>';
function tableMetric(m) {
  if (!m || !m.connected) return '<span class="muted">未接入</span>';
  return `<span class="table-reading">${esc(nfmt(m.value))}<small>${esc(m.unit)}</small></span><div class="table-sub">${sourceCN(m.source)} · ${m.expired ? '<span class="pill warn">已过期</span>' : esc(fmtTime(m.collected_at))}</div>`;
}
function renderPonds() {
  const query = $("#pondSearch").value.trim().toLowerCase();
  const rows = pondRows.filter(({ p }) =>
    `${p.name} ${p.code}`.toLowerCase().includes(query),
  );
  $("#tableCount").textContent =
    `显示 ${rows.length} / ${pondRows.length} 口授权鱼塘`;
  if (!rows.length) {
    $("#ponds").innerHTML = '<div class="no-results">没有找到匹配的鱼塘</div>';
    return;
  }
  $("#ponds").innerHTML =
    `<table class="pond-table"><thead><tr><th>鱼塘 / 批次</th><th>水温</th><th>溶氧</th><th>今日投喂</th><th>终端状态</th><th>待处理</th><th></th></tr></thead><tbody>${rows
      .map(({ p, d, error }) => {
        const today = d?.today,
          env = d?.env || {},
          alerts = d?.pending_alerts;
        return `<tr><td><div class="pond-name"><span class="pond-symbol">${pondMark}</span><div><b>${esc(p.name)}</b><small>${esc(p.code)} · ${esc(p.active_batch?.code || "暂无批次")}</small></div></div></td><td>${error ? "读取失败" : tableMetric(env.temperature)}</td><td>${error ? "—" : tableMetric(env.oxygen)}</td><td>${today ? `<span class="table-reading">${nfmt(today.actual_feed_kg)}<small>kg</small></span><div class="table-sub">${today.unknown_tasks || 0} 个任务量待核实</div>` : "—"}</td><td><span class="pill ${p.terminal?.status === "online" ? "ok" : "warn"}">${p.terminal ? "终端" + statusCN(p.terminal.status) : "未注册"}</span></td><td>${alerts ? `<span class="pill ${alerts.length ? "bad" : ""}">${alerts.length ? alerts.length + " 条异常" : "暂无异常"}</span>` : "—"}</td><td><a class="btn gray sm" href="/pond/${p.id}">管理鱼塘 ↗</a></td></tr>`;
      })
      .join("")}</tbody></table>`;
}
async function loadOverview() {
  const button = $("#refreshOverview");
  button.disabled = true;
  try {
    const [ponds, alerts, tasks] = await Promise.all([
      API.get("/api/ponds"),
      API.get("/api/alerts?status=all"),
      API.get("/api/tasks?status=locked"),
    ]);
    if (!ponds.ok) throw new Error(ponds.message || "鱼塘数据加载失败");
    pondRows = await Promise.all(
      ponds.data.map(async (p) => {
        const r = await API.get(`/api/ponds/${p.id}/dashboard`);
        return { p, d: r.ok ? r.data : null, error: !r.ok };
      }),
    );
    const valid = pondRows.filter((x) => x.d),
      partial = valid.length !== pondRows.length;
    $("#overviewError").hidden = !partial;
    $("#overviewError").className = "overview-error";
    if (partial)
      $("#overviewError").textContent =
        "部分鱼塘数据读取失败；汇总仅包含已加载记录，请刷新重试。";
    $("#totalPonds").textContent = pondRows.length;
    $("#pondSummary").textContent =
      `${pondRows.filter((x) => x.p.active_batch).length} 个在养批次 · 当前身份可见`;
    $("#totalFeed").textContent = valid.length
      ? nfmt(valid.reduce((a, x) => a + (x.d.today.actual_feed_kg || 0), 0))
      : "—";
    const unknown = valid.reduce(
      (a, x) => a + (x.d.today.unknown_tasks || 0),
      0,
    );
    $("#feedSummary").textContent =
      `${unknown} 个任务实际量待核实${partial ? " · 部分统计" : ""}`;
    const pending = alerts.ok
      ? alerts.data.filter(
          (a) => a.status === "open" || a.status === "processing",
        )
      : null;
    $("#totalAlerts").textContent = pending ? pending.length : "—";
    const online = pondRows.filter(
      (x) => x.p.terminal?.status === "online",
    ).length;
    $("#onlineTerminals").textContent = online;
    $("#terminalDenominator").textContent =
      `/ ${pondRows.filter((x) => x.p.terminal).length} 台`;
    $("#terminalSummary").textContent =
      `${pondRows.filter((x) => x.p.terminal && x.p.terminal.status !== "online").length} 台离线 · 服务端状态`;
    $("#attentionCount").textContent = pending ? pending.length : "—";
    const nameFor = (id) =>
      pondRows.find((x) => x.p.id === id)?.p.name || "系统";
    const titles = {
      feed_fail: "投喂执行异常",
      receipt_timeout: "投喂结果待核查",
      data_invalid: "环境数据异常",
      receipt_conflict: "回执结论冲突",
    };
    $("#attentionList").innerHTML = pending
      ? pending
          .slice(0, 3)
          .map(
            (a) =>
              `<a class="attention-item" href="${a.kind?.startsWith("receipt") ? "/review" : "/alerts"}"><span class="attention-symbol ${a.level === "error" ? "red" : ""}">!</span><span class="attention-copy"><b>${esc(nameFor(a.pond_id))} · ${esc(titles[a.kind] || "异常待处理")}</b><p>${esc(a.message)}</p><time>${esc(fmtTime(a.created_at))}</time></span></a>`,
          )
          .join("") ||
        '<div class="empty">当前没有待处理异常<br><small>可以继续查看环境与投喂记录</small></div>'
      : '<div class="empty">待办读取失败，请刷新重试</div>';
    $("#reviewSummary").textContent = tasks.ok
      ? `${tasks.data.length} 个占用设备的任务`
      : "核查列表读取失败";
    const badge = $("#navReview");
    badge.hidden = !tasks.ok || !tasks.data.length;
    if (!badge.hidden) badge.textContent = tasks.data.length;
    $("#pondCount").textContent = pondRows.length;
    const previous = $("#trendPond").value;
    $("#trendPond").innerHTML = pondRows
      .map(
        ({ p }) =>
          `<option value="${p.id}">${esc(p.name)} · ${esc(p.code)}</option>`,
      )
      .join("");
    if (pondRows.some((x) => String(x.p.id) === previous))
      $("#trendPond").value = previous;
    $("#lastUpdated").textContent =
      "最近更新 " + new Date().toLocaleTimeString("zh-CN", { hour12: false });
    renderPonds();
    await loadTrend();
  } catch (e) {
    $("#overviewError").className = "overview-error";
    $("#overviewError").hidden = false;
    $("#overviewError").textContent = e.message;
    $("#ponds").innerHTML =
      '<div class="empty">数据暂时不可用，请点击刷新数据重试。</div>';
  } finally {
    button.disabled = false;
  }
}
async function loadTrend() {
  const request = ++trendRequest,
    id = $("#trendPond").value;
  const days = Number($("#trendDays").value),
    end = new Date(),
    start = new Date(end.getTime() - days * 86400000);
  $("#trendChart").innerHTML = '<div class="loading-state">读取趋势…</div>';
  if (!id) {
    $("#trendChart").innerHTML = '<div class="empty">暂无可查看的鱼塘</div>';
    return;
  }
  const local = (d) =>
    new Date(d.getTime() - d.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 19);
  const r = await API.get(
    `/api/ponds/${id}/env/history?metric=${metric}&start=${encodeURIComponent(local(start))}&end=${encodeURIComponent(local(end))}`,
  );
  if (request !== trendRequest) return;
  const rows = r.ok
    ? r.data
        .filter((x) => Number.isFinite(x.value))
        .sort((a, b) => a.collected_at.localeCompare(b.collected_at))
    : [];
  const valid = rows.filter((x) => x.valid),
    latest = valid.at(-1),
    unit = metric === "temperature" ? "℃" : "mg/L";
  $("#trendValue").textContent = latest ? nfmt(latest.value) : "—";
  $("#trendUnit").textContent = unit;
  $("#trendLegend").textContent =
    metric === "temperature" ? "水温测量" : "溶氧测量";
  $("#trendDetail").textContent = latest
    ? `区间最近记录 · ${fmtTime(latest.collected_at)}`
    : "暂无有效记录";
  $("#trendSource").textContent = rows.length
    ? `${[...new Set(rows.map((x) => sourceCN(x.source)))].join(" / ")} · ${valid.length} 条有效样本 · 间隔超过 ${days === 1 ? 2 : 8} 小时断线`
    : "未获取数据";
  if (!valid.length) {
    $("#trendChart").innerHTML =
      `<div class="empty">${esc(r.ok ? "此时间范围内没有有效测量" : r.message || "趋势读取失败")}</div>`;
    return;
  }
  const w = 640,
    h = 190,
    p = { l: 34, r: 12, t: 12, b: 25 };
  const lo = Math.min(...valid.map((x) => x.value)),
    hi = Math.max(...valid.map((x) => x.value));
  const pad = Math.max((hi - lo) * 0.25, 0.3),
    min = lo - pad,
    max = hi + pad;
  const tx = (t) =>
    p.l +
    ((new Date(t.replace(" ", "T")).getTime() - start.getTime()) /
      (end - start)) *
      (w - p.l - p.r);
  const ty = (v) => p.t + ((max - v) / (max - min)) * (h - p.t - p.b);
  let svg = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${metric === "temperature" ? "水温" : "溶氧"}历史趋势，横轴为实际时间"><defs><linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#a8c08a" stop-opacity=".3"/><stop offset="1" stop-color="#a8c08a" stop-opacity=".01"/></linearGradient></defs>`;
  for (let i = 0; i < 4; i++) {
    const y = p.t + ((h - p.t - p.b) * i) / 3;
    svg += `<path d="M${p.l} ${y}H${w - p.r}" stroke="#e9eee1" stroke-dasharray="3 4"/><text x="${p.l - 7}" y="${y + 3}" text-anchor="end" fill="#8e9a80" font-size="9">${(max - ((max - min) * i) / 3).toFixed(1)}</text>`;
  }
  // Dots preserve irregular sampling and leave missing intervals unconnected.
  svg += valid
    .map(
      (x) =>
        `<circle cx="${tx(x.collected_at)}" cy="${ty(x.value)}" r="2.5" fill="#729752"><title>${esc(x.collected_at)} · ${x.value} ${unit} · ${sourceCN(x.source)}</title></circle>`,
    )
    .join("");
  // Only connect adjacent valid records inside a stated display gap; never bridge invalid points.
  const gap = days === 1 ? 2 * 3600000 : 8 * 3600000;
  for (let i = 1; i < rows.length; i++) {
    const a = rows[i - 1],
      b = rows[i];
    if (
      a.valid &&
      b.valid &&
      new Date(b.collected_at.replace(" ", "T")) -
        new Date(a.collected_at.replace(" ", "T")) <=
        gap
    ) {
      svg += `<path d="M${tx(a.collected_at)} ${ty(a.value)}L${tx(b.collected_at)} ${ty(b.value)}" stroke="#729752" stroke-width="1.8" fill="none"/>`;
    }
  }
  for (let i = 0; i < 5; i++) {
    const d = new Date(start.getTime() + ((end - start) * i) / 4);
    const label =
      days === 1
        ? d.toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })
        : `${d.getMonth() + 1}/${d.getDate()}`;
    svg += `<text x="${p.l + ((w - p.l - p.r) * i) / 4}" y="${h - 4}" text-anchor="middle" font-size="9" fill="#8e9a80">${label}</text>`;
  }
  $("#trendChart").innerHTML = svg + "</svg>";
}
document.addEventListener("DOMContentLoaded", () => {
  $("#todayLabel").textContent = localToday().replaceAll("-", " / ");
  $("#refreshOverview").onclick = loadOverview;
  $("#pondSearch").oninput = renderPonds;
  $("#trendPond").onchange = loadTrend;
  $("#trendDays").onchange = loadTrend;
  $$("[data-metric]").forEach(
    (b) =>
      (b.onclick = () => {
        metric = b.dataset.metric;
        $$("[data-metric]").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)),
        );
        loadTrend();
      }),
  );
  loadOverview();
});
