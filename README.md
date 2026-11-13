# goblinvest

A system for managing personal finances.

This is the Web UI, but it's powered by a libary of python tools in this repo called [`goblinvest_core`](goblinvest_core/), which can be used in isolation.

## Running locally

```bash
uv sync
uv run uvicorn goblinvest.main:app --reload --port 8080
```

Then open <http://127.0.0.1:8080> and sign up. 

## Encryption

Signing up encrypts your vault and its adjustments files with your account password,
unless you clear the checkbox on the form. Your password is held in the server's memory
and never written to disk, so restarting the server locks every vault; the app then asks
for the password again on the next page that needs it. **There is no password reset** —
an encrypted vault cannot be opened without its password.

`GV_VAULT_TTL_MINUTES` (default 480) is how long an unused vault stays unlocked.

## Running with Docker

```bash
cp .env.example .env      # set GV_UID/GV_GID to your own: id -u ; id -g
mkdir -p data
docker compose up -d --build
```

The container listens on 127.0.0.1 by default, on the assumption that a reverse proxy
goes in front of it. Set `GV_ENV=prod` once that proxy serves HTTPS, and not before —
it marks the session cookie `Secure`, which stops logins working over plain http.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```
