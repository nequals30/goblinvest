# syntax=docker/dockerfile:1

# ---- build: install everything into /app/.venv ------------------------------
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Third-party dependencies first, from the lockfile alone. goblinvest-core is a
# path dependency whose source isn't in the context yet, so it is skipped by
# name here -- without that, uv tries to build it and fails. This layer is the
# ~250MB one (pandas, yfinance, cryptography, sqlcipher) and it is rebuilt only
# when uv.lock changes, which is what keeps a source-only edit a small push.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-install-package goblinvest-core

# Then both projects' own source. --no-editable copies them into the venv
# rather than linking back to /app/src, so the runtime stage needs nothing but
# .venv -- the wheel carries templates/ and static/ with it.
COPY goblinvest_core/ ./goblinvest_core/
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime ----------------------------------------------------------------
# Same Debian release and same Python minor as the build stage, deliberately:
# the venv holds cp314 compiled extensions and an absolute interpreter path, so
# a mismatch here breaks it. Note Alpine is not an option -- sqlcipher3-binary
# publishes manylinux x86_64 wheels only, nothing for musl and nothing for ARM.
FROM python:3.14-slim-trixie

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GV_DATA_DIR=/data

# Not root. compose overrides this with the host's own uid so a bind-mounted
# data folder stays writable and stays owned by a real person.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home app \
    && mkdir -p /data && chown app:app /data

COPY --from=build --chown=app:app /app/.venv /app/.venv

USER app
WORKDIR /app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()"]

# One worker on purpose. SQLite plus core's vault (no WAL yet -- request #4)
# means several worker processes would contend on the same files; the single
# process plus uvicorn's threadpool is the shape the rest of the app assumes.
CMD ["uvicorn", "goblinvest.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
