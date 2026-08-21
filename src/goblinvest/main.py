"""App factory. Run with:

uv run uvicorn goblinvest.main:app --reload --port 8080
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from goblinvest.auth import NotAuthenticated
from goblinvest.db import init_db
from goblinvest.routes import auth as auth_routes
from goblinvest.routes import pages, vault_settings
from goblinvest.routes import settings as settings_routes
from goblinvest.templates import STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Importing core pulls in pandas, which costs ~330ms. Pay it at boot so the
    # first page load doesn't; without this, the first /month is 340ms and every
    # one after it is 7ms, which reads as "the app is slow" on exactly the
    # request a person is most likely to notice.
    import goblinvest_core  # noqa: F401

    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Goblinvest", lifespan=lifespan)

    @app.exception_handler(NotAuthenticated)
    async def _to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(vault_settings.router)
    app.include_router(pages.router)
    return app


app = create_app()
