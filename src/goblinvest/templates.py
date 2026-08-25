"""The shared Jinja2 environment."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from goblinvest.auth import CSRF_FIELD

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def csrf_input(request: Request) -> Markup:
    """The hidden field every form on a signed-in page needs, as `{{ csrf_input }}`.

    The token is put on `request.state` by the `current_user` dependency. A page
    rendered without one (there are none today) gets an empty value, which
    `csrf_guard` then rejects — the failure is a refused POST, never a silently
    unprotected form.
    """
    token = getattr(request.state, "csrf_token", "")
    return Markup('<input type="hidden" name="{}" value="{}" />').format(CSRF_FIELD, token)


def page(
    request: Request, name: str, user: Any, nav: Any, *, status_code: int = 200, **context: Any
):
    """Render a signed-in page. `user` and `nav` are what base.html's chrome needs."""
    return templates.TemplateResponse(
        request,
        name,
        {"user": user, "nav": nav, "csrf_input": csrf_input(request), **context},
        status_code=status_code,
    )
