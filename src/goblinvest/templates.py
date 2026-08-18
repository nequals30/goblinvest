"""The shared Jinja2 environment."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def page(
    request: Request, name: str, user: Any, nav: Any, *, status_code: int = 200, **context: Any
):
    """Render a signed-in page. `user` and `nav` are what base.html's chrome needs."""
    return templates.TemplateResponse(
        request, name, {"user": user, "nav": nav, **context}, status_code=status_code
    )
