function buildTree(rows) {
  const byAccount = new Map();
  let grandTotal = 0;

  for (const r of rows) {
    const acct = r.account_name ?? "Uncategorized";
    const asset = r.asset_name ?? "(unknown asset)";
    const mv = Number(r.market_value ?? 0);

    grandTotal += mv;

    if (!byAccount.has(acct)) {
      byAccount.set(acct, { name: acct, market_value: 0, _children: [] });
    }
    const acctNode = byAccount.get(acct);
    acctNode.market_value += mv;
    acctNode._children.push({ name: asset, market_value: mv });
  }

  const accountNodes = Array.from(byAccount.values()).sort((a,b)=>a.name.localeCompare(b.name));

  return [{
    name: "Total",
    market_value: grandTotal,
    _children: accountNodes,
    _rowType: "total",
  }];
}

async function loadAccounts() {
  const res = await fetch("/api/summarize_accounts");
  if (!res.ok) throw new Error("HTTP " + res.status);
  const data = await res.json();

  new Tabulator("#accounts-table", {
    layout: "fitColumns",
    dataTree: true,
    dataTreeStartExpanded: [true, false],
    dataTreeElementColumn: "name",
    data: buildTree(data),
    columns: [
      { title: "Name", field: "name" },
      { title: "Market Value", field: "market_value", hozAlign: "right", formatter: "money" },
    ],
    rowFormatter: (row) => {
      const d = row.getData();
      if (d._rowType === "total") row.getElement().style.fontWeight = "700";
    },
  });
}

loadAccounts().catch(err => {
  console.error(err);
  document.querySelector("#accounts-table").innerText = "Failed to load accounts.";
});
