"""Servergerenderte Jinja2-Templates mit sicherem Basiskontext.

Der Kontext enthält CSRF-Token, Flash-Nachrichten und `url_for`; sensitive
Daten werden nie automatisch eingebettet. Templates laufen mit Autoescape (XSS).
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request
from starlette.responses import HTMLResponse

from .flash import pop_flashes
from .middleware import CSRF_KEY, SESSION_KEY
from .money import format_money
from .security import new_csrf_token

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(default_for_string=True, default=True),
    auto_reload=True,
)
_env.globals["format_money"] = format_money
_env.globals["fmt_dt"] = (
    lambda dt: dt.strftime("%Y-%m-%d %H:%M") + " UTC" if dt else "—"
)


def ensure_csrf(request: Request) -> str:
    token = request.session.get(CSRF_KEY)
    if not isinstance(token, str) or not token:
        token = new_csrf_token()
        request.session[CSRF_KEY] = token
    return token


def render(request: Request, name: str, *, status_code: int = 200, **context) -> HTMLResponse:
    settings = getattr(request.app.state, "settings", None)
    context.update(
        {
            "request": request,
            "csrf_token": ensure_csrf(request),
            "flashes": pop_flashes(request),
            "is_logged_in": bool(request.session.get(SESSION_KEY)),
            "wecatalog_enabled": bool(getattr(settings, "wecatalog_enabled", False)),
            "url_for": request.url_for,
        }
    )
    html = _env.get_template(name).render(context)
    return HTMLResponse(html, status_code=status_code)
