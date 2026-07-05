# goblinvest-core

A library for analyzing personal finances. This is the core set of tools used by Goblinvest, but can be used as an independent library.

This is a port of `PersonalFinance.jl`, which was a Julia prototype of this concept.

It works by loading CSVs with transactions from all accounts into one big ledger called a "Vault" (which is just a SQLite database).

Then, there are tools for populating the Vault with market data and analyzing the data, including the ability to calculate net worth accurately to the penny historically.

Additionally, there are tools for encrypting the vault file, and encrypting the raw CSVs (in a way that still keeps them portable, and able to be version-controlled with git).

## Try It

```python
from goblinvest_core import Vault

v = Vault.create("~/finance/PersonalFinanceVault.db")
v.add_account("checking", account_group_name="cash")
v.list_accounts()
```

Vaults can optionally be encrypted on disk with SQLCipher:

```python
v = Vault.create("~/finance/PersonalFinanceVault.db", encrypted=True)
```

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
