# API reference

Everything except file encryption is a method on a `Vault` object: create or
open a vault, call its methods, close it. `v.add_account(...)` means "call
`add_account` on the vault `v`".

::: goblinvest_core.Vault

## File encryption

Encrypting files on disk — statement CSVs above all — and the password they
share with encrypted vaults.

::: goblinvest_core.encrypt_file
    options:
      heading_level: 3

::: goblinvest_core.read_encrypted_file
    options:
      heading_level: 3

::: goblinvest_core.decrypt_file
    options:
      heading_level: 3

::: goblinvest_core.ask_password
    options:
      heading_level: 3

::: goblinvest_core.forget_password
    options:
      heading_level: 3
