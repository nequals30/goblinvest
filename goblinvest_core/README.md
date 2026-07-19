# goblinvest-core

A library for analyzing personal finances. This is the core set of tools used by Goblinvest, but can be used as an independent library.

This is a port of `PersonalFinance.jl`, which was a Julia prototype of this concept.

It works by loading CSVs with transactions from all accounts into one big ledger called a "Vault" (which is just a SQLite database).

Then, there are tools for populating the Vault with market data and analyzing the data, including the ability to calculate net worth accurately to the penny historically.

Additionally, there are tools for encrypting the both the vault and the raw CSVs.

## Try It

```python
from goblinvest_core import Vault

v = Vault.create("~/finance/PersonalFinanceVault.db")

# Register accounts and assets, then record transactions.
# A brokerage buy is two rows: the dollars leaving and the shares arriving.
v.add_account("checking", account_group_name="cash")
v.add_account("brokerage", account_group_name="investments")
v.add_asset("VTI")

v.add_transactions(
    "brokerage",
    ["2026-07-02", "2026-07-02"],
    ["buy VTI", "buy VTI"],
    [-1000.00, 3.2],
    assets=["USD", "VTI"],
)

# Pull daily prices from Yahoo Finance, then analyze.
v.populate_yfinance_prices("VTI")

v.summarize_accounts()        # what you hold right now, at the latest prices
v.accumulate_mv().sum(axis=1) # daily net worth, first transaction through today
```

Loading the same transactions twice never double-counts, so one script can rebuild the whole vault from your statement CSVs at any time. Joint accounts, stock splits, and dividends are all handled.

A complete end-to-end example — fake statements loaded into a vault, priced, and summarized into a net-worth history — lives in [`examples/load_transactions/`](examples/load_transactions/).

## Encryption

Both Vaults and the raw CSVs can be encrypted:

Vaults: 

```python
v = Vault.create("~/finance/PersonalFinanceVault.db", encrypted=True)
```

CSVs:

```python
import pandas as pd
from goblinvest_core import encrypt_file, read_encrypted_file

encrypt_file("statements/2026-06.csv")  # once, when the statement arrives

df = pd.read_csv(read_encrypted_file("statements/2026-06.csv"))  # ever after
```

For CSVs, the `read_encrypted_file()` reads the contents without re-encrypting the file. That makes it easy to version control the files (e.g. in Git), since they don't change every time you read them.

You will be prompted for the password at a hidden terminal prompt, which is remembered in memory for 15 minutes. The same password is used for the encryption of the vault and the CSV files.

## Documentation

Public-API docs (inputs, outputs, examples) live in `docs/` and build with
[MkDocs](https://www.mkdocs.org/):

```
uv run mkdocs serve   # browsable docs at http://127.0.0.1:8000
```

## Development

```
uv sync
uv run pytest
```
