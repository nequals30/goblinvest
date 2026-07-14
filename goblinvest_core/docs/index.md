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

## Balances and net-worth history

`summarize_accounts` is the "what do I hold right now" view — every non-zero position,
valued at its latest known price, with the price's date shown so a stale quote is
visible:

```python
v.summarize_accounts()
#   account_name account_group_name asset    units  price price_date  ownership_share  market_value last_transaction
# 0    brokerage        investments  NVDA      3.2  159.3 2026-07-10              1.0        509.76       2026-07-02
# 1    brokerage        investments   USD  -1000.0    1.0        NaT              1.0      -1000.00       2026-07-02
# 2     checking               cash   USD   3936.8    1.0        NaT              1.0       3936.80       2026-07-03
```

`accumulate_mv` is the same idea through time: for every day from your first
transaction to today, the market value of each position — units held that day times
that day's price. Total net worth is the row sum:

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
`"account_group_name"`. A held asset with no stored price shows as `NaN` — run
`populate_yfinance_prices` for it.

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

## Encrypted statement CSVs

The raw statement CSVs are the source of truth, which makes them the most sensitive
files you have — worth keeping in a (private) git repository, and worth encrypting at
rest. Encrypt each statement once, then read it forever without touching its bytes:

```python
import pandas as pd
from goblinvest_core import encrypt_file, read_encrypted_file

encrypt_file("statements/chase_2026-06.csv")        # once, when the statement arrives

df = pd.read_csv(read_encrypted_file("statements/chase_2026-06.csv"))   # ever after
```

`read_encrypted_file` decrypts into memory only — the file on disk never changes, so
git never sees phantom modifications, no matter how many times your rebuild script
runs.

Encrypted files are built to be hard to break by accident. They are stored as plain
text, so the small liberties other programs take with text files — an editor adding a
newline when you save, git changing line endings between operating systems — do no
harm. And if a file really is damaged, or you type the wrong password, you get a
clear error instead of garbage rows loading into your vault.

It is the same password as the vault, remembered for the same 15 minutes: one
`ask_password()` covers loading a hundred statements.

Need to fix a bad row? `decrypt_file` writes the plaintext back in place — edit it,
then `encrypt_file` again:

```python
from goblinvest_core import decrypt_file

decrypt_file("statements/chase_2026-06.csv")
# ...fix the row in your editor...
encrypt_file("statements/chase_2026-06.csv")
```

??? note "Technical details"

    An encrypted file is a `GVENC1` header line followed by the encrypted payload as
    base64 text (like PGP's "ASCII armor"); reading strips all whitespace before
    decoding, which is why editors and line-ending conversions can't hurt it. The
    payload is a 16-byte salt, a 12-byte nonce, and AES-256-GCM ciphertext — GCM's
    built-in integrity tag is what turns a wrong password or a damaged file into a
    clean error. The key is derived from your password with PBKDF2-HMAC-SHA256
    (600,000 iterations) and cached in memory; files encrypted in the same session
    share one salt, so a script reading hundreds of statements pays the deliberately
    slow key derivation once, not per file.

See the [API reference](api.md) for every function, its inputs, and its outputs.
