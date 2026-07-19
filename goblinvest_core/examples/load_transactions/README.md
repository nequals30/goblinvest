# Example: loading transactions

A self-contained walkthrough of goblinvest-core. Loads fake but realistic
statement CSVs into a SQLite vault, fetches market prices, and prints a
current snapshot plus a daily net-worth time series.

## Running

From the package root, generate the fake statements, then load them:

```bash
uv run examples/load_transactions/generate_fake_data.py
uv run examples/load_transactions/load_vault.py
```

`generate_fake_data.py` writes the CSVs to `data/` in this folder — that
location is gitignored on purpose: statement CSVs, even fake ones, don't
belong in a code repository. The script is deterministic (fixed RNG seed),
so regenerating always produces the same files.

`load_vault.py` reads those CSVs, creates `PersonalFinanceVault.db` in this
folder (also gitignored), registers the accounts and assets, loads every
CSV, pulls Yahoo Finance prices (requires internet), and prints a snapshot
and a net-worth time series.

Both scripts accept custom paths, so other tools can build the demo vault
wherever they need it:

```bash
uv run examples/load_transactions/generate_fake_data.py --data-dir /tmp/demo_statements
uv run examples/load_transactions/load_vault.py \
    --data-dir /tmp/demo_statements \
    --vault /tmp/PersonalFinanceVault.db \
    --overwrite
```

`--overwrite` replaces an existing vault file without the interactive
prompt.

## The data

The CSVs represent the transactions of a US-based 30-something with:

| Account          | Type                                         | Ownership |
|------------------|----------------------------------------------|-----------|
| `checking`       | personal bank account, paycheck deposits     | 1.0       |
| `joint-checking` | shared with a partner, pays rent & groceries | 0.5       |
| `credit-card`    | day-to-day spending, paid in full monthly    | 1.0       |
| `brokerage`      | self-directed: USD, VFIAX, VBTLX, AAPL       | 1.0       |

Transactions cover 2023-01-01 through 2026-05-31, with monthly
cross-account flows (`checking → joint-checking` on the 5th and
`checking → brokerage` on the 16th).

Bank-style accounts ship CSVs with columns `date, description, amount`; the
brokerage CSVs have `date, description, units, asset` — one folder per
account, one file per year.
