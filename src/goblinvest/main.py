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
from goblinvest.routes import pages
from goblinvest.routes import settings as settings_routes
from goblinvest.templates import STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Goblinvest", lifespan=lifespan)

    @app.exception_handler(NotAuthenticated)
    async def _to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(pages.router)
    return app


app = create_app()
