"""Flash-Nachrichten über die Session + gemeinsame Formular-Helfer."""
from __future__ import annotations

from starlette.requests import Request

FLASH_KEY = "_flashes"


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session.setdefault(FLASH_KEY, []).append({"level": level, "text": message})


def pop_flashes(request: Request) -> list[dict]:
    return list(request.session.pop(FLASH_KEY, []))
