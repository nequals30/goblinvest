# API reference

Everything except file encryption is a method on a `Vault` object: create or
open a vault, call its methods, close it. `v.add_account(...)` means "call
`add_account` on the vault `v`".

::: goblinvest_core.Vault
    options:
      members: []
      heading_level: 2

## Creating and opening a vault

::: goblinvest_core.Vault.create

::: goblinvest_core.Vault.open

::: goblinvest_core.Vault.close

::: goblinvest_core.Vault.adjustments_dir

## Accounts

::: goblinvest_core.Vault.add_account

::: goblinvest_core.Vault.list_accounts

::: goblinvest_core.Vault.delete_account

## Assets

::: goblinvest_core.Vault.add_asset

::: goblinvest_core.Vault.list_assets

::: goblinvest_core.Vault.delete_asset

## Transactions

::: goblinvest_core.Vault.add_transactions

::: goblinvest_core.Vault.list_transactions

## Categories

::: goblinvest_core.Vault.add_category

::: goblinvest_core.Vault.list_categories

::: goblinvest_core.Vault.delete_category

::: goblinvest_core.Vault.apply_categories

::: goblinvest_core.Vault.set_category_rule

::: goblinvest_core.Vault.set_category_exception

::: goblinvest_core.Vault.list_uncategorized

## Market prices

::: goblinvest_core.Vault.populate_yfinance_prices

::: goblinvest_core.Vault.get_asset_prices

## Balances and net worth

::: goblinvest_core.Vault.accumulate_mv

::: goblinvest_core.Vault.summarize_accounts

::: goblinvest_core.Vault.summarize_vault

## File encryption

Encrypting files on disk — statement CSVs above all — and the password they
share with encrypted vaults.

::: goblinvest_core.encrypt_file

::: goblinvest_core.read_encrypted_file

::: goblinvest_core.decrypt_file

::: goblinvest_core.ask_password

::: goblinvest_core.forget_password
