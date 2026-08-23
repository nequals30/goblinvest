# goblinvest

A system for managing personal finances.

This is the Web UI, but it's powered by a libary of python tools in this repo called [`goblinvest_core`](goblinvest_core/), which can be used in isolation.

## Running locally

```bash
uv sync
uv run uvicorn goblinvest.main:app --reload --port 8080
```

Then open <http://127.0.0.1:8080> and sign up. 

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
