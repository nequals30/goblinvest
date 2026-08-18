# goblinvest

A system for managing personal finances.

This is the Web UI, but it's powered by a libary of python tools in this repo called [`goblinvest_core`](goblinvest_core/), which can be used in isolation.

## Running locally

```bash
uv sync
uv run uvicorn goblinvest.main:app --reload --port 8080
```

Then open <http://127.0.0.1:8080> and sign up. 

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```
