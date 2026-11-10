"""Einstellungen als Key-Value-Speicher (AppSetting).

Ein Feld: Box-Standard-VK – dient als Label-Angabe im Uhr-Formular
(„Box optional (+X €)“) und als Vorschlag im Verkaufsdialog.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import BOX_OPTIONS, AppSetting
from .money import format_money

KEY_BOX_DEFAULT_VK = "box_default_vk_cents"


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.flush()


def box_default_vk_cents(db: Session) -> int | None:
    raw = get_setting(db, KEY_BOX_DEFAULT_VK)
    if raw is None:
        return 5000  # bewährter Standard, solange nichts eingestellt wurde
    try:
        return int(raw)
    except ValueError:
        return 5000


def box_default_vk_display(db: Session) -> str:
    """Deutsch formatierter Betrag ohne Währungssuffix (für Eingabefelder)."""
    return format_money(box_default_vk_cents(db) or 0).replace(" €", "")


def box_options_for(db: Session) -> dict[str, str]:
    """BOX_OPTIONS mit dynamischem VK für die Option „optional“."""
    options = dict(BOX_OPTIONS)
    options["optional"] = (
        f"Box optional (+{format_money(box_default_vk_cents(db) or 0)})"
    )
    return options
