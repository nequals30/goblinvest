# API reference

Everything for the vault is reached through a `Vault` object: create or open one, call
its methods, close it. `v.add_account(...)` means "call `add_account` on the vault `v`".

::: goblinvest_core.Vault

## Encryption

Keeping files — statement CSVs above all — encrypted on disk, and the password that
they and encrypted vaults share. Passwords are typed at a hidden prompt and remembered
in memory for 15 minutes; they are never passed in code or stored on disk.

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
