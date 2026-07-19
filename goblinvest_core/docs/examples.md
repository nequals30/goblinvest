# Examples

The repository's [`examples/`](https://github.com/nequals30/goblinvest/tree/main/goblinvest_core/examples)
folder holds runnable, self-contained demos.

## Loading transactions

`examples/load_transactions/` is an end-to-end walkthrough: fake but
realistic statement CSVs — a US-based 30-something with a checking account,
a joint account shared with a partner, a credit card, and a brokerage
holding VFIAX, VBTLX and AAPL — loaded into a vault, priced from Yahoo
Finance, and summarized.

From the package root, generate the statements, then load them:

```bash
uv run examples/load_transactions/generate_fake_data.py
uv run examples/load_transactions/load_vault.py
```

The first script writes three and a half years of CSVs (one folder per
account, one file per year) from a fixed random seed. The second creates
`PersonalFinanceVault.db` in the example's folder, registers the accounts
and assets, feeds every CSV to
[`add_transactions`][goblinvest_core.Vault.add_transactions], fetches
prices with
[`populate_yfinance_prices`][goblinvest_core.Vault.populate_yfinance_prices]
(requires internet), and prints a
[`summarize_accounts`][goblinvest_core.Vault.summarize_accounts] snapshot
plus a daily net-worth series from
[`accumulate_mv`][goblinvest_core.Vault.accumulate_mv].

Both scripts take `--data-dir`, and the loader also takes `--vault` and
`--overwrite`, so the demo can be built anywhere:

```bash
uv run examples/load_transactions/load_vault.py \
    --data-dir /tmp/demo_statements --vault /tmp/PersonalFinanceVault.db --overwrite
```

The loader is a template for the real thing: a "rebuild the world" script
over your own statements looks just like it — create the vault with
`overwrite=True`, register accounts and assets, loop over CSVs, load, price.
Because every load is idempotent, running it start to finish is always safe.
