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
