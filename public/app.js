// ----- shared helpers ------------------------------------------------------

const fmtUSD = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 0,
});
const fmtUSDc = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 2,
});

// ----- Tabulator: accounts summary -----------------------------------------

function buildTree(rows) {
  const byAccount = new Map();
  let grandTotal = 0;

  const maxDate = (a, b) => {
    if (!a) return b ?? null;
    if (!b) return a ?? null;
    return a > b ? a : b;
  };

  for (const r of rows) {
    const acct = r.account_name ?? "Uncategorized";
    const asset = r.asset_name ?? "(unknown asset)";
    const mv = Number(r.market_value ?? 0);

    grandTotal += mv;
    const tx = r.most_recent_trans_date ?? null;

    if (!byAccount.has(acct)) {
      byAccount.set(acct, {
        name: acct,
        market_value: 0,
        most_recent_trans_date: null,
        _children: [],
        _assetMap: new Map(),
      });
    }
    const acctNode = byAccount.get(acct);
    acctNode.market_value += mv;
    acctNode.most_recent_trans_date = maxDate(acctNode.most_recent_trans_date, tx);

    let assetNode = acctNode._assetMap.get(asset);
    if (!assetNode) {
      assetNode = { name: asset, market_value: 0, most_recent_trans_date: null };
      acctNode._assetMap.set(asset, assetNode);
      acctNode._children.push(assetNode);
    }
    assetNode.market_value += mv;
    assetNode.most_recent_trans_date = maxDate(assetNode.most_recent_trans_date, tx);
  }

  const accountNodes = Array.from(byAccount.values())
    .sort((a, b) => a.name.localeCompare(b.name));
  const totalTx = accountNodes.reduce(
    (acc, n) => maxDate(acc, n.most_recent_trans_date), null);

  return [{
    name: "Total",
    market_value: grandTotal,
    most_recent_trans_date: totalTx,
    _children: accountNodes,
    _rowType: "total",
  }];
}

let accountsTable = null;

async function loadAccounts() {
  const res = await fetch("/api/summarize_accounts", { credentials: "same-origin" });
  if (res.status === 401) { showAuth(); return; }
  if (!res.ok) throw new Error("HTTP " + res.status);
  const data = await res.json();

  if (accountsTable) accountsTable.destroy();
  accountsTable = new Tabulator("#accounts-table", {
    layout: "fitColumns",
    dataTree: true,
    dataTreeStartExpanded: [true, false],
    dataTreeElementColumn: "name",
    data: buildTree(data),
    columns: [
      { title: "Name", field: "name" },
      { title: "Market Value", field: "market_value", hozAlign: "right", formatter: "money" },
      { title: "Most Recent", field: "most_recent_trans_date", sorter: "string", hozAlign: "right" },
    ],
    rowFormatter: (row) => {
      const d = row.getData();
      if (d._rowType === "total") row.getElement().style.fontWeight = "700";
    },
  });
}

// ----- ECharts: stacked-area market-value chart ----------------------------

let mvChart = null;
let mvAllNames = [];
let mvIsolated = null;
let mvApplyingProgrammatic = false;

function updateIsolatedLabel() {
  const el = document.getElementById("mv-isolated-label");
  if (!el) return;
  if (mvIsolated == null) {
    el.hidden = true;
    el.textContent = "";
  } else {
    el.hidden = false;
    el.textContent = "showing only " + mvIsolated;
  }
}

function setIsolation(name) {
  if (!mvChart || mvAllNames.length === 0) return;
  const selected = {};
  for (const n of mvAllNames) selected[n] = (name == null) || (n === name);
  mvApplyingProgrammatic = true;
  mvChart.setOption({ legend: [{ selected }] });
  mvApplyingProgrammatic = false;
  mvIsolated = name;
  updateIsolatedLabel();
}

function renderMvChart(payload) {
  const dates = payload.dates || [];
  const series = payload.series || [];
  const chartEl = document.getElementById("mv-chart");
  const emptyEl = document.getElementById("mv-empty");

  if (dates.length === 0 || series.length === 0) {
    if (mvChart) { mvChart.dispose(); mvChart = null; }
    chartEl.hidden = true;
    emptyEl.hidden = false;
    mvAllNames = [];
    mvIsolated = null;
    updateIsolatedLabel();
    return;
  }

  chartEl.hidden = false;
  emptyEl.hidden = true;

  // Net = per-date sum of all series values; rendered as overlay line.
  const net = new Array(dates.length).fill(0);
  for (const s of series) {
    for (let i = 0; i < dates.length; i++) {
      const v = s.values[i];
      if (v != null) net[i] += v;
    }
  }

  const NET_NAME = "Net";
  mvAllNames = [...series.map(s => s.name), NET_NAME];
  mvIsolated = null;
  updateIsolatedLabel();

  if (!mvChart) mvChart = echarts.init(chartEl);

  // ECharts default categorical palette (5.x). Used to give the pos/neg
  // halves of one source series the same color, so the shared legend
  // entry is unambiguous.
  const palette = [
    "#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
    "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc",
  ];

  // For each source series, split into a positive half and a negative half.
  // Both halves share `name` — ECharts groups them under one legend entry
  // that toggles them together. Each half lives in only one stack ('pos'
  // or 'neg'), so no series ever jumps stacks and there's no zig-zag.
  const echartsSeries = [];
  series.forEach((s, idx) => {
    const color = palette[idx % palette.length];
    let hasPos = false, hasNeg = false;
    const posData = new Array(dates.length);
    const negData = new Array(dates.length);
    for (let i = 0; i < dates.length; i++) {
      const v = s.values[i];
      if (v == null) { posData[i] = 0; negData[i] = 0; continue; }
      if (v > 0)      { posData[i] = v; negData[i] = 0; hasPos = true; }
      else if (v < 0) { posData[i] = 0; negData[i] = v; hasNeg = true; }
      else            { posData[i] = 0; negData[i] = 0; }
    }
    const baseSeries = {
      name: s.name,
      type: "line",
      showSymbol: false,
      lineStyle: { width: 1, color },
      itemStyle: { color },
      areaStyle: { color, opacity: 0.6 },
      emphasis: { focus: "series" },
    };
    if (hasPos) echartsSeries.push({ ...baseSeries, stack: "pos", data: posData });
    if (hasNeg) echartsSeries.push({ ...baseSeries, stack: "neg", data: negData });
    // If a series is exactly zero across all dates, skip it entirely.
  });

  echartsSeries.push({
    name: NET_NAME,
    type: "line",
    showSymbol: false,
    smooth: false,
    lineStyle: { width: 2.5, color: "#000" },
    itemStyle: { color: "#000" },
    emphasis: { focus: "self", lineStyle: { width: 3 } },
    z: 10,
    data: net,
  });

  mvChart.setOption({
    animation: false,
    grid: { left: 70, right: 24, top: 64, bottom: 84 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line" },
      valueFormatter: (v) => (v == null ? "—" : fmtUSDc.format(v)),
      order: "valueDesc",
    },
    legend: {
      type: "scroll",
      top: 0,
      data: mvAllNames,
      selected: Object.fromEntries(mvAllNames.map(n => [n, true])),
    },
    toolbox: {
      right: 10,
      feature: {
        dataZoom: { yAxisIndex: "none" },
        restore: {},
        saveAsImage: { name: "goblinvest-net-worth" },
      },
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: dates,
    },
    yAxis: {
      type: "value",
      axisLabel: { formatter: (v) => fmtUSD.format(v) },
      splitLine: { lineStyle: { color: "#eee" } },
    },
    dataZoom: [
      { type: "inside", start: 0, end: 100 },
      { type: "slider", start: 0, end: 100, height: 22, bottom: 30 },
    ],
    series: echartsSeries,
  }, { notMerge: true });

  // Click-to-isolate via legend
  mvChart.off("legendselectchanged");
  mvChart.on("legendselectchanged", (e) => {
    if (mvApplyingProgrammatic) return;
    const clicked = e.name;
    if (clicked === mvIsolated) {
      setIsolation(null);
    } else {
      setIsolation(clicked);
    }
  });
}

async function loadMvChart() {
  const res = await fetch("/api/accumulate_mv", { credentials: "same-origin" });
  if (res.status === 401) { showAuth(); return; }
  if (!res.ok) throw new Error("HTTP " + res.status);
  const payload = await res.json();
  renderMvChart(payload);
}

function disposeMvChart() {
  if (mvChart) { mvChart.dispose(); mvChart = null; }
  mvAllNames = [];
  mvIsolated = null;
  updateIsolatedLabel();
}

window.addEventListener("resize", () => { if (mvChart) mvChart.resize(); });

// ----- view switching ------------------------------------------------------

const els = {
  topbar:   () => document.getElementById("topbar"),
  who:      () => document.getElementById("who"),
  auth:     () => document.getElementById("auth-view"),
  dash:     () => document.getElementById("dash-view"),
  form:     () => document.getElementById("auth-form"),
  submit:   () => document.getElementById("auth-submit"),
  errorBox: () => document.getElementById("auth-error"),
  tabs:     () => document.querySelectorAll(".tab"),
};

let authMode = "login";

function showAuth() {
  els.topbar().hidden = true;
  els.dash().hidden = true;
  els.auth().hidden = false;
  disposeMvChart();
  clearError();
  els.form().reset();
  els.form().username.focus();
}

function showDash(user) {
  els.auth().hidden = true;
  els.topbar().hidden = false;
  els.dash().hidden = false;
  els.who().textContent = user.username;

  Promise.allSettled([
    loadAccounts(),
    loadMvChart(),
  ]).then(results => {
    results.forEach((r, i) => {
      if (r.status === "rejected") {
        console.error(["loadAccounts", "loadMvChart"][i], r.reason);
      }
    });
  });
}

function setMode(mode) {
  authMode = mode;
  els.tabs().forEach(t => t.classList.toggle("is-active", t.dataset.mode === mode));
  els.submit().textContent = mode === "signup" ? "sign up" : "log in";
  els.form().password.autocomplete = mode === "signup" ? "new-password" : "current-password";
  clearError();
}

function showError(msg) {
  const box = els.errorBox();
  box.textContent = msg;
  box.hidden = false;
}
function clearError() {
  const box = els.errorBox();
  box.textContent = "";
  box.hidden = true;
}

// ----- API calls -----------------------------------------------------------

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch (_) {}
  return { ok: res.ok, status: res.status, data };
}

async function checkMe() {
  const res = await fetch("/api/me", { credentials: "same-origin" });
  if (res.ok) {
    const data = await res.json();
    return data.user;
  }
  return null;
}

// ----- handlers ------------------------------------------------------------

els.tabs().forEach(t => {
  t.addEventListener("click", () => setMode(t.dataset.mode));
});

els.form().addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  const fd = new FormData(els.form());
  const username = String(fd.get("username") || "").trim();
  const password = String(fd.get("password") || "");
  const path = authMode === "signup" ? "/api/signup" : "/api/login";

  els.submit().disabled = true;
  try {
    const r = await apiPost(path, { username, password });
    if (!r.ok) {
      showError((r.data && r.data.error) || `request failed (${r.status})`);
      return;
    }
    showDash(r.data.user);
  } catch (err) {
    showError(String(err));
  } finally {
    els.submit().disabled = false;
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  showAuth();
});

document.getElementById("mv-reset").addEventListener("click", () => setIsolation(null));

// ----- boot ----------------------------------------------------------------

(async function boot() {
  const user = await checkMe();
  if (user) showDash(user); else showAuth();
})();
