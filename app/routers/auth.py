"""Login/Logout für den Einzelbetreiber (keine hartcodierten Zugangsdaten)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..csrf import verify_csrf
from ..middleware import CSRF_KEY, SESSION_KEY
from ..security import new_csrf_token, verify_password
from ..templating import render

router = APIRouter(tags=["auth"])


def _safe_next(raw: str | None) -> str:
    if raw and raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return "/"


@router.get("/login")
def login_page(request: Request):
    if request.session.get(SESSION_KEY):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", next_to=_safe_next(request.query_params.get("next")))


@router.post("/login", dependencies=[Depends(verify_csrf)])
async def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    next_to: str = Form(default="/"),
):
    settings = request.app.state.settings
    user_ok = username.strip().lower() == settings.app_username.strip().lower()
    if user_ok and verify_password(settings.admin_password_hash, password):
        request.session.clear()
        request.session[SESSION_KEY] = settings.operator_id
        request.session[CSRF_KEY] = new_csrf_token()
        return RedirectResponse(_safe_next(next_to), status_code=303)
    return render(
        request,
        "login.html",
        status_code=401,
        error="Passwort falsch.",
        next_to=_safe_next(next_to),
    )


@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
