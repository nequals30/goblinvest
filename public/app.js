// ----- Tabulator: accounts summary -----------------------------------------

function buildTree(rows) {
  const byAccount = new Map();
  let grandTotal = 0;

  const maxDate = (a, b) => {
    if (!a) return b ?? null;
    if (!b) return a ?? null;
    return a > b ? a : b; // works for "YYYY-MM-DD"
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

let authMode = "login"; // "login" | "signup"

function showAuth() {
  els.topbar().hidden = true;
  els.dash().hidden = true;
  els.auth().hidden = false;
  clearError();
  els.form().reset();
  els.form().username.focus();
}

function showDash(user) {
  els.auth().hidden = true;
  els.topbar().hidden = false;
  els.dash().hidden = false;
  els.who().textContent = user.username;
  loadAccounts().catch(err => {
    console.error(err);
    document.getElementById("accounts-table").innerText = "Failed to load accounts.";
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
  try { data = await res.json(); } catch (_) { /* empty body */ }
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

// ----- boot ----------------------------------------------------------------

(async function boot() {
  const user = await checkMe();
  if (user) showDash(user); else showAuth();
})();
