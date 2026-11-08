"""Vorlagenverwaltung: Liste, Editor, Validierung, Vorschau, Override-Reset.

- Alle Aktionen nur für eine Whitelist bekannter Dateinamen (kein Traversal).
- Speichern/Löschen schreibt ausschließlich in LISTING_TEMPLATE_DIR (Daten-
  Ordner/Volume) – die mitgelieferten Built-ins in app/listing_templates/
  bleiben unangetastet und gelten als Fallback (ChoiceLoader).
- Jede Vorlage wird vor dem Speichern syntaktisch geprüft und gegen einen
  Beispielkontext gerendert (StrictUndefined) – fehlende/unkritische Variablen
  fallen dadurch sofort auf. Die Vorschau zeigt das Ergebnis zum Kopieren.
- Neue Plattformen brauchen weiterhin Code (Enum/Generator/Hinweise); die UI
  verwaltet die Vorlagen der vier hinterlegten Plattformen.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from jinja2 import Environment, StrictUndefined, TemplateError

from ..csrf import verify_csrf
from ..deps import SettingsDep
from ..flash import flash
from ..models import ListingPlatform
from ..services.listing_generator import DEFAULT_TEMPLATE_DIR, TEMPLATE_CONTEXT_KEYS, sample_context
from ..templating import render

router = APIRouter(tags=["templates"])

_MUTATE = [Depends(verify_csrf)]
MAX_TEMPLATE_BYTES = 200_000

TEMPLATE_NAMES: frozenset[str] = frozenset(
    {f"{p.value}.txt.j2" for p in ListingPlatform}
    | {f"{p.value}.title.txt.j2" for p in ListingPlatform}
    | {"hints.json"}
)


def _check_name(name: str) -> str:
    if name not in TEMPLATE_NAMES:
        raise HTTPException(status_code=400, detail="Unbekannter Vorlagenname.")
    return name


def _load(settings, name: str) -> tuple[str, str]:
    """(Inhalt, Quelle) – Override hat Vorrang vor Built-in."""
    override = settings.listing_template_dir / name
    if override.is_file():
        return override.read_text(encoding="utf-8"), "override"
    built_in = DEFAULT_TEMPLATE_DIR / name
    if built_in.is_file():
        return built_in.read_text(encoding="utf-8"), "builtin"
    return "", "none"


def _validate(name: str, content: str) -> str:
    """Wirft HTTPException(400) mit verständlicher Meldung bei Problemen."""
    if len(content.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=400, detail="Vorlage ist zu groß (max. 200.000 Byte).")
    if name == "hints.json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Ungültiges JSON: {exc}") from exc
        if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()
        ):
            raise HTTPException(
                status_code=400, detail="Erwartet wird ein JSON-Objekt aus Textwerten."
            )
        return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    try:
        environment = Environment(undefined=StrictUndefined)
        template = environment.from_string(content)
        template.render(**sample_context())
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=f"Vorlagenfehler: {exc}") from exc
    return content


def _edit_context(settings, name: str, **extra) -> dict:
    content, source = _load(settings, name)
    context = {
        # "tpl_name" statt "name": render() belegt "name" bereits (Templatepfad).
        "tpl_name": name,
        "content": extra.pop("content", content),
        "source": extra.pop("source", source),
        "is_hints": name == "hints.json",
        "available_keys": sorted(TEMPLATE_CONTEXT_KEYS),
        "errors": {},
        "preview_output": None,
        "names": sorted(TEMPLATE_NAMES),
        "template_dir": str(settings.listing_template_dir),
    }
    context.update(extra)
    return context


@router.get("/templates")
def templates_list(request: Request, settings=SettingsDep):
    rows = []
    for name in sorted(TEMPLATE_NAMES):
        _, source = _load(settings, name)
        rows.append({"name": name, "source": source})
    return render(
        request,
        "templates_list.html",
        rows=rows,
        template_dir=str(settings.listing_template_dir),
    )


@router.get("/templates/edit")
def templates_edit(request: Request, settings=SettingsDep, name: str = "reddit.txt.j2"):
    name = _check_name(name)
    return render(request, "templates_edit.html", **_edit_context(settings, name))


@router.post("/templates/preview", dependencies=_MUTATE)
async def templates_preview(request: Request, settings=SettingsDep):
    form = await request.form()
    name = _check_name(str(form.get("name") or ""))
    content = str(form.get("content") or "")
    try:
        normalized = _validate(name, content)
        if name == "hints.json":
            output = normalized
        else:
            environment = Environment(undefined=StrictUndefined)
            output = environment.from_string(normalized).render(**sample_context())
    except HTTPException as exc:
        return render(
            request,
            "templates_edit.html",
            status_code=400,
            **_edit_context(settings, name, content=content, errors={"content": str(exc.detail)}),
        )
    return render(
        request,
        "templates_edit.html",
        **_edit_context(settings, name, content=content, preview_output=output),
    )


@router.post("/templates/save", dependencies=_MUTATE)
async def templates_save(request: Request, settings=SettingsDep):
    form = await request.form()
    name = _check_name(str(form.get("name") or ""))
    content = str(form.get("content") or "")
    try:
        normalized = _validate(name, content)
    except HTTPException as exc:
        return render(
            request,
            "templates_edit.html",
            status_code=400,
            **_edit_context(settings, name, content=content, errors={"content": str(exc.detail)}),
        )
    target = settings.listing_template_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(normalized, encoding="utf-8")
    flash(request, f"Vorlage {name} gespeichert (Override).", "success")
    return Response(status_code=303, headers={"location": f"/templates/edit?name={name}"})


@router.post("/templates/delete", dependencies=_MUTATE)
async def templates_delete(request: Request, settings=SettingsDep):
    form = await request.form()
    name = _check_name(str(form.get("name") or ""))
    target = settings.listing_template_dir / name
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Kein Override zum Löschen vorhanden.")
    target.unlink()
    flash(request, f"Override {name} entfernt – ausgelieferte Vorlage wieder aktiv.", "info")
    return Response(status_code=303, headers={"location": f"/templates/edit?name={name}"})


__all__ = ["router"]
