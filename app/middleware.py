"""Auth-Gate, Login-Sichtschutz und Security-Headers als ASGI-Middleware.

Der Auth-Gate gilt global (Allowlist statt pro Route), damit keine interne
Route versehentlich ungeschützt veröffentlicht wird. Öffentlich sind nur
Login, Healthcheck und statische Assets – es gibt bewusst KEINE öffentliche
Bestands- oder Daten-API. CSRF wird zusätzlich pro POST-Route über
`app.csrf.verify_csrf` geprüft (Route-Dependency, zuverlässig bei Uploads).
"""
from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PUBLIC_PATHS = frozenset({"/login", "/healthz"})
SESSION_KEY = "uid"
CSRF_KEY = "csrf"

_GATE_HTML = b"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>Nicht angemeldet</title></head><body><p>Nicht angemeldet.
<a href="/login">Zum Login</a></p></body></html>"""
_GATE_JSON = b'{"detail":"Nicht authentifiziert."}'


def _path(scope: Scope) -> str:
    return scope.get("path", "/") or "/"


def _is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return path.startswith("/static/")


class AuthGateMiddleware:
    """Leitet nicht authentifizierte Anfragen an /login um (bzw. 401)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_public(_path(scope)):
            await self.app(scope, receive, send)
            return

        session = scope.get("session") or {}
        if session.get(SESSION_KEY):
            await self.app(scope, receive, send)
            return

        accept = _header_value(scope, b"accept") or ""
        path = _path(scope)
        if "application/json" in accept:
            status, headers = 401, [(b"www-authenticate", b"Session")]
            body = _GATE_JSON
            headers.append((b"content-type", b"application/json"))
        elif "text/html" in accept or not accept:
            target = f"/login?next={path}".encode()
            if path.startswith("/login"):
                target = b"/login"
            status, headers = 303, [(b"location", target)]
            body = _GATE_HTML
        else:
            status, headers = 401, [(b"www-authenticate", b"Session")]
            body = _GATE_HTML
        await _send_simple(send, status, headers, body)


class SecurityHeadersMiddleware:
    """Kompakte Security-Headers für alle Antworten."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_static = _path(scope).startswith("/static/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append(
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; "
                        b"style-src 'self'; style-src-attr 'unsafe-inline'; "
                        b"img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; "
                        b"form-action 'self'",
                    )
                )
                # Statische Assets immer revalidieren (heuristische Browser-
                # Caches wie Safari würden altes CSS nach einem Image-Update
                # weiter zeigen; ETag/Last-Modified liefern dann 304).
                if is_static and not any(k == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-cache"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class LoginRateLimitMiddleware:
    """Einfacher In-Memory-Sichtschutz für den Login (pro Client-IP)."""

    def __init__(self, app: ASGIApp, *, max_attempts: int, window_seconds: int) -> None:
        self.app = app
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _limited(self, key: str) -> bool:
        now = monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        return len(hits) >= self.max_attempts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != "/login":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        key = client[0] if client else "unknown"
        method = scope.get("method", "GET")

        if method == "POST" and self._limited(key):
            await _send_simple(
                send,
                429,
                [(b"retry-after", str(self.window_seconds).encode())],
                b"<!DOCTYPE html><html lang=de><body><p>Zu viele Login-Versuche. "
                b"Bitte sp&auml;ter erneut versuchen.</p></body></html>",
            )
            return

        async def send_wrapper(message: Message) -> None:
            if (
                method == "POST"
                and message["type"] == "http.response.start"
                and message["status"] >= 400
            ):
                self._hits[key].append(monotonic())
            await send(message)

        await self.app(scope, receive, send_wrapper)


def session_uid(scope: Scope) -> str | None:
    session = scope.get("session") or {}
    uid = session.get(SESSION_KEY)
    return uid if isinstance(uid, str) else None


def _header_value(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


async def _send_simple(
    send: Send, status: int, headers: list[tuple[bytes, bytes]], body: bytes
) -> None:
    base = [
        (b"content-type", b"text/html; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    await send({"type": "http.response.start", "status": status, "headers": base + headers})
    await send({"type": "http.response.body", "body": body})
