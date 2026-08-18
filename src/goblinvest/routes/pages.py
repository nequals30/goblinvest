"""Pages that aren't auth forms. Today: the welcome page and a healthcheck."""

from fastapi import APIRouter, Request

from goblinvest.auth import CurrentUser
from goblinvest.templates import templates

router = APIRouter()


@router.get("/")
def home(request: Request, user: CurrentUser):
    return templates.TemplateResponse(request, "welcome.html", {"user": user})


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
