"""App factory. Run with:

uv run uvicorn goblinvest.main:app --reload --port 8080
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from goblinvest.auth import CsrfInvalid, NotAuthenticated, csrf_guard
from goblinvest.config import settings
from goblinvest.db import init_db
from goblinvest.routes import auth as auth_routes
from goblinvest.routes import pages, vault_settings
from goblinvest.routes import settings as settings_routes
from goblinvest.templates import STATIC_DIR, templates


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
    # The interactive docs are handy while JSON endpoints don't exist yet, but
    # they're a map of every route for anyone who finds the server, so prod
    # turns them off. Nothing else reads these three arguments.
    hidden = {"docs_url": None, "redoc_url": None, "openapi_url": None}
    doc_urls = hidden if settings().is_prod else {}

    # csrf_guard is registered on the app, not on each router: a POST route
    # added later is protected by existing, not by remembering to opt in.
    app = FastAPI(
        title="Goblinvest",
        lifespan=lifespan,
        dependencies=[Depends(csrf_guard)],
        **doc_urls,
    )

    @app.exception_handler(NotAuthenticated)
    async def _to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(CsrfInvalid)
    async def _csrf_failed(request: Request, exc: CsrfInvalid):
        # Realistically this is a form left open across a logout, so the page
        # says what to do rather than just refusing.
        return templates.TemplateResponse(
            request, "csrf.html", {}, status_code=status.HTTP_403_FORBIDDEN
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(vault_settings.router)
    app.include_router(pages.router)
    return app


app = create_app()
