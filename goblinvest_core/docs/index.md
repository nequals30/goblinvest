# goblinvest-core

A library for understanding your personal finances — balances, net-worth history, and
(coming) investment returns and taxes — computed from the complete history of your own
transactions.

It is built on one idea: **raw statement CSVs are the source of truth**. Statements from
your bank, brokerage, and credit cards load into a SQLite database (the "vault") that
all analysis runs against, and the vault is a disposable build artifact: rebuildable
from scratch, idempotently, with one script. Loading the same CSV twice never
double-counts. If a row is wrong, fix the CSV and rebuild — the vault is never edited in
place.

## Install

```bash
uv add goblinvest-core
```

## Quickstart

```python
from goblinvest_core import Vault

v = Vault.create("~/finance/MyVault.db")

v.add_account("checking", account_group_name="cash")
v.add_account("joint-checking", ownership_share=0.5, account_group_name="cash")

v.list_accounts()
#    account_id    account_name  ownership_share account_group_name
# 0           1        checking              1.0               cash
# 1           2  joint-checking              0.5               cash

v.close()
```

## Recording transactions

Every movement of money (or shares, or anything else) is a transaction: a signed
amount of one asset, in one account, on one date. A brokerage purchase is two rows —
the dollars leaving and the shares arriving:

```python
v.add_account("brokerage", account_group_name="investments")
v.add_asset("VTI")

v.add_transactions(
    "brokerage",                        # one account name applies to all rows
    ["2026-07-02", "2026-07-02"],
    ["buy VTI", "buy VTI"],
    [-1000.00, 3.2],
    assets=["USD", "VTI"],              # omit for the base currency
)

v.list_transactions()
#    transaction_id account_name       date description   amount asset  ownership_share account_group_name
# 0               1    brokerage 2026-07-02     buy VTI -1000.00   USD              1.0        investments
# 1               2    brokerage 2026-07-02     buy VTI     3.20   VTI              1.0        investments
```

Loading the same transactions twice never double-counts, so a script that rebuilds the
vault from all your statement CSVs can be re-run start to finish at any time.

## Market prices

Assets that trade publicly are priced straight from Yahoo Finance — name your assets
with their Yahoo ticker symbols and call:

```python
v.populate_yfinance_prices(["NVDA", "VTI"])
```

Each asset's daily closing prices are fetched from its first transaction through today
and stored in the vault; re-running just fills in the days since the last run. Two
things make the stored prices match what your brokerage statement said *at the time*:

- **Splits are un-adjusted.** Yahoo rewrites history after a stock split — after
  NVDA's 2024 ten-for-one split, a June-2023 close of ~$420 is served as ~$42. The
  vault stores the price as it traded that day, so shares held × price on any date
  agrees with the statement from that date.
- **Dividends are not deducted.** A dividend arrives in your ledger as a cash
  transaction when you load the statement CSV, so prices must not also account for it.

Read prices back as a grid — one row per date you ask for, one column per asset:

```python
v.get_asset_prices(["2026-07-03", "2026-07-04"], ["USD", "NVDA"])
#                USD    NVDA
# date
# 2026-07-03    1.0  159.34
# 2026-07-04    1.0  159.34   <- market closed: last known price carried forward
```

The base currency is always exactly 1.0. Dates with no quote (weekends, holidays)
carry the last known price forward — pass `fill_missing_with_stale=False` to get `NaN`
instead. Dates before an asset's first known price are `NaN` either way.

A vault can also be used in a `with` block, which closes it automatically:

```python
with Vault.open("~/finance/MyVault.db") as v:
    accounts = v.list_accounts()
```

## Encrypted vaults

Pass `encrypted=True` when creating a vault and the file on disk is encrypted with
[SQLCipher](https://www.zetetic.net/sqlcipher/). Opening it requires the same password;
without it the file is unreadable (it is not even recognizable as a SQLite database).

The password is never written in code — you type it at a hidden terminal prompt, so it
can't leak through your scripts or shell history. Once entered, it is remembered in
memory for 15 minutes, so working with a vault doesn't mean retyping the password at
every step:

```python
from goblinvest_core import Vault, ask_password

ask_password()   # type it once (prompted and confirmed, nothing echoed)

v = Vault.create("~/finance/MyVault.db", encrypted=True)   # no prompt: remembered
v.close()

v = Vault.open("~/finance/MyVault.db")   # encryption is detected automatically
```

Calling `ask_password()` up front is optional — `create` and `open` prompt on their own
when they need a password and none is remembered. `forget_password()` clears the
remembered password immediately.

Leave `encrypted` off and the vault is a plain SQLite file, readable by any SQLite
tool, and never involves a password or a prompt.

See the [API reference](api.md) for every function, its inputs, and its outputs.
