# API reference

Everything is reached through a `Vault` object: create or open one, call its methods,
close it. `v.add_account(...)` means "call `add_account` on the vault `v`".

::: goblinvest_core.Vault

## Passwords

For encrypted vaults. Passwords are typed at a hidden prompt and remembered in memory
for 15 minutes — they are never passed in code or stored on disk.

::: goblinvest_core.ask_password

::: goblinvest_core.forget_password
