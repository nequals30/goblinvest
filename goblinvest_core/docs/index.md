# goblinvest-core

A Python library for personal finances. You load every transaction from your
statement CSVs into a SQLite file — the **vault** — and the library computes
balances, net-worth history, and (coming) investment returns and taxes.

A vault holds your transactions — each a signed amount of one asset, in one
account, on one date — and prices for the assets that trade publicly. Your own
decisions about those transactions, such as which category each belongs to, live
beside the vault in CSV files you can edit, called **adjustments**.

Loading the same transactions twice never double-counts them, so a script that
loads all of your statements can be re-run at any time.

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

A vault can also be used in a `with` block, which closes it for you:

```python
with Vault.open("~/finance/MyVault.db") as v:
    accounts = v.list_accounts()
```

## Transactions

Accounts and assets are registered before transactions can refer to them. An
asset is anything you hold an amount of: the base currency (`USD` unless you
say otherwise), a ticker, another currency.

A brokerage purchase is two transactions on the same date — the dollars leaving
and the shares arriving:

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

`summarize_vault` counts what the vault holds and shows the dates its ledger
spans:

```python
v.summarize_vault()
# {'n_transactions': 1215,
#  'n_accounts': 6,
#  'n_assets': 9,
#  'n_categories': 12,
#  'n_uncategorized': 84,
#  'first_transaction': Timestamp('2016-01-04 00:00:00'),
#  'last_transaction': Timestamp('2026-08-18 00:00:00')}
```

## Market prices

Name an asset with its Yahoo Finance ticker symbol and its daily closing prices
can be fetched and stored in the vault:

```python
v.populate_yfinance_prices(["NVDA", "VTI"])
```

Prices run from the asset's first transaction to today; re-running fills in the
days since the last run. Stored prices are what the asset actually traded at on
the day, so shares held × price agrees with the statement from that date. (Yahoo
rewrites its own history after a stock split; that rewriting is undone here.)

Read prices back as a grid — one row per date, one column per asset:

```python
v.get_asset_prices(["2026-07-03", "2026-07-04"], ["USD", "NVDA"])
#                USD    NVDA
# date
# 2026-07-03    1.0  159.34
# 2026-07-04    1.0  159.34   <- market closed: last known price carried forward
```

The base currency is always exactly 1.0. Dates with no quote (weekends,
holidays) carry the last known price forward — pass
`fill_missing_with_stale=False` for `NaN` instead. Dates before an asset's first
known price are `NaN` either way.

## Balances and net worth

`summarize_accounts` gives current holdings: every non-zero position, valued at
its latest known price, with that price's date so a stale quote is visible.

```python
v.summarize_accounts()
#   account_name account_group_name asset    units  price price_date  ownership_share  market_value last_transaction
# 0    brokerage        investments  NVDA      3.2  159.3 2026-07-10              1.0        509.76       2026-07-02
# 1    brokerage        investments   USD  -1000.0    1.0        NaT              1.0      -1000.00       2026-07-02
# 2     checking               cash   USD   3936.8    1.0        NaT              1.0       3936.80       2026-07-03
```

`accumulate_mv` gives the same thing for every day from your first transaction
to today. Net worth is the row sum:

```python
mv = v.accumulate_mv()                 # one column per account::asset pair
mv.sum(axis=1)                         # net worth, daily

v.accumulate_mv(group_by="account_group_name")
#                cash  investments
# date
# 2026-07-02  3976.80      -490.24
# 2026-07-03  3936.80      -493.13
```

`group_by` buckets the columns by `"account_name"`, `"asset"`, or
`"account_group_name"`. An asset held with no stored price shows as `NaN` — run
`populate_yfinance_prices` for it.

## Categories

Every transaction has exactly one category, or counts as `"unclassified"` until
it gets one. Categories are defined and assigned in CSV files kept in the
vault's **adjustments folder**; the vault's categories are rebuilt from those
files, never the other way around.

| File | Columns | What it does |
| --- | --- | --- |
| `categories.csv` | `category` | lists the categories you have defined |
| `category_rules.csv` | `pattern,category` | categorizes every transaction whose description equals the pattern |
| `category_exceptions.csv` | `account,date,description,amount,asset,category` | categorizes one exact transaction |

`Vault.create` creates the folder, and the vault remembers where it is, so no
other call has to name it:

```python
v = Vault.create("~/finance/MyVault.db")   # folder created beside the vault
# or keep it with your statements:
v = Vault.create("~/finance/MyVault.db", adjustments_dir="~/statements/adjustments")
```

Define your categories, then apply them:

```python
v.create_category(["groceries", "rent", "travel", "streaming"])

orphans = v.apply_categories()
v.list_transactions()      # now has a `category` column
```

Both calls are safe to re-run, so they belong in your load script. Using a
category that was never defined is an error rather than a new category;
capitalization is ignored, and the spelling in `categories.csv` is the one you
get back. `apply_categories` returns any exception rows that no longer match a
transaction, which is what a bank rewording its descriptions looks like.

Categorize as you go with `set_category_rule`, which writes the rule into the
file for you. It covers every transaction with that description, including ones
in statements you have not loaded yet:

```python
v.set_category_rule("Netflix", "streaming")
v.set_category_rule(["Trader Joe's", "SAFEWAY #1042"], "groceries")   # or a batch
```

Rules nearer the bottom of the file win, so reclassifying something is another
call. To categorize a single transaction whatever the rules say, use
`set_category_exception`. It takes the same parallel lists as
`add_transactions`, and a category of `None` removes the exception again:

```python
v.set_category_exception("checking", "2026-02-14", "CHECK # 1145", -500.00, "gifts")
```

`list_uncategorized()` is the to-do list: everything still unclassified, grouped
by description, sorted so the row covering the most transactions comes first.
`list_categories()` lists every category with its transaction count.

??? note "Technical details"

    Descriptions match exactly, ignoring capitalization. Transactions that are
    identical within one load are stored with `" (2)"`-style suffixes; rules
    ignore the suffix, so a rule for `Netflix` also covers `Netflix (2)`, while
    an exception matches the description verbatim, suffix included. File edits
    are written to a temporary file and swapped into place, so an interrupted
    write cannot corrupt them.

## Encryption

Passwords are never passed in code. You type one at a hidden terminal prompt and
it is remembered in memory for 15 minutes, so a script that reads a hundred
files asks once. `ask_password()` asks up front; `forget_password()` clears it
immediately. Anything that needs a password prompts on its own if none is
remembered.

### Vaults

Pass `encrypted=True` and the vault file is encrypted with
[SQLCipher](https://www.zetetic.net/sqlcipher/). Opening it needs the same
password; without it the file is unreadable. The adjustments files are encrypted
along with it.

```python
from goblinvest_core import Vault, ask_password

ask_password()

v = Vault.create("~/finance/MyVault.db", encrypted=True)
v.create_category(["streaming", "gifts"])
v.set_category_rule("Netflix", "streaming")
v.close()

v = Vault.open("~/finance/MyVault.db")   # encryption is detected automatically
```

No call changes when things are encrypted: there is no password argument, no
decrypting step, and no plaintext copy to remember to delete. Leave `encrypted`
off and the vault is a plain SQLite file, readable by any SQLite tool, with no
prompt.

### Statement CSVs

Statement CSVs can be encrypted too. Encrypt each one once, then read it
without changing its bytes:

```python
import pandas as pd
from goblinvest_core import encrypt_file, read_encrypted_file

encrypt_file("statements/chase_2026-06.csv")        # once, when it arrives

df = pd.read_csv(read_encrypted_file("statements/chase_2026-06.csv"))   # ever after
```

`read_encrypted_file` decrypts into memory only, so a git repository of
encrypted statements stays clean however often your load script runs. To edit a
file, `decrypt_file` it in place, then `encrypt_file` it again. A wrong password
or a damaged file gives a clear error rather than garbage rows.

Encrypted files are stored as plain text, so the liberties other programs take
with text files — an editor adding a newline, git converting line endings — do
no harm.

??? note "Technical details"

    An encrypted file is a `GVENC1` header line followed by the payload as
    base64 text (like PGP's "ASCII armor"); reading strips all whitespace before
    decoding, which is why editors and line-ending conversions cannot hurt it.
    The payload is a 16-byte salt, a 12-byte nonce, and AES-256-GCM ciphertext,
    whose integrity tag is what turns a wrong password or a damaged file into a
    clean error. The key is derived with PBKDF2-HMAC-SHA256 (600,000
    iterations) and cached in memory; files encrypted in one session share a
    salt, so the deliberately slow derivation is paid once per session rather
    than once per file.

## Next

See the [examples](examples.md) for a complete statements-to-net-worth script,
and the [API reference](api.md) for every function, its inputs, and its outputs.
