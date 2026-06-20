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
		_assetMap: new Map(), // helper to aggregate per-asset
	  });
	}
	const acctNode = byAccount.get(acct);

	acctNode.market_value += mv;
	acctNode.most_recent_trans_date = maxDate(acctNode.most_recent_trans_date, tx);

	// aggregate per asset (sum mv, take max tx)
	let assetNode = acctNode._assetMap.get(asset);
	if (!assetNode) {
	  assetNode = { name: asset, market_value: 0, most_recent_trans_date: null };
	  acctNode._assetMap.set(asset, assetNode);
	  acctNode._children.push(assetNode);
	}
	assetNode.market_value += mv;
	assetNode.most_recent_trans_date = maxDate(assetNode.most_recent_trans_date, tx);

  }

  const accountNodes = Array.from(byAccount.values()).sort((a,b)=>a.name.localeCompare(b.name));

	const totalTx = accountNodes.reduce((acc, n) => maxDate(acc, n.most_recent_trans_date), null);

	return [{
	  name: "Total",
	  market_value: grandTotal,
	  most_recent_trans_date: totalTx,
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
  { title: "Most Recent", field: "most_recent_trans_date", sorter: "string", hozAlign: "right" },
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
