"""Datenbank-Anbindung (SQLite über SQLAlchemy 2.0)."""
from __future__ import annotations

import contextlib
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    """Basis für alle ORM-Modelle."""


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def ensure_schema(engine: Engine) -> None:
    """Idempotentes Nachrüsten fehlender Spalten (create_all legt nur neue an)."""
    import uuid

    from sqlalchemy import inspect, text

    from .models import DEFAULT_COST_CATEGORIES

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("watches")}
    statements: list[str] = []

    for ddl in (
        "ALTER TABLE watches ADD COLUMN factory VARCHAR(80)",
        "ALTER TABLE watches ADD COLUMN version VARCHAR(40)",
        "ALTER TABLE watches ADD COLUMN band VARCHAR(80)",
        "ALTER TABLE watches ADD COLUMN highlight VARCHAR(160)",
        "ALTER TABLE watches ADD COLUMN purchase_price_usd_cents INTEGER",
        "ALTER TABLE watches ADD COLUMN usd_eur_rate_milli INTEGER",
        "ALTER TABLE watches ADD COLUMN purchase_batch VARCHAR(80)",
    ):
        name = ddl.rsplit(" ", 1)[-1]
        if name not in columns:
            statements.append(ddl)

    # Umzug der alten Zusatzkosten-Einzelsumme in die Positionstabelle
    # (nur wenn die Altspalte existiert und die Uhr noch keine Position hat):
    if "extra_costs_cents" in columns:
        statements.append(
            """INSERT INTO watch_extra_costs (watch_id, category, amount_cents, note)
               SELECT w.id, 'sonstiges', w.extra_costs_cents, w.extra_costs_note
               FROM watches w
               WHERE w.extra_costs_cents IS NOT NULL AND w.extra_costs_cents > 0
                 AND NOT EXISTS (SELECT 1 FROM watch_extra_costs e WHERE e.watch_id = w.id)"""
        )

    # Entfallene Freitextfelder entfernen (SQLite >= 3.35; sonst bleibt die
    # Spalte liegen – der Code nutzt sie dann schlicht nicht mehr):
    for column in ("condition_description", "included_accessories",
                   "extra_costs_cents", "extra_costs_note"):
        if column in columns:
            statements.append(f"ALTER TABLE watches DROP COLUMN {column}")

    with engine.begin() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception:  # noqa: BLE001 – z. B. alte SQLite ohne DROP COLUMN
                continue

        # ---- V3 Kosten: Währung, Basis/%, Kategorie-Tabelle ----
        cost_cols = {c["name"] for c in inspector.get_columns("watch_extra_costs")}
        for col, ddl in (
            (
                "currency",
                "ALTER TABLE watch_extra_costs ADD COLUMN currency "
                "VARCHAR(3) NOT NULL DEFAULT 'EUR'",
            ),
            ("category_id", "ALTER TABLE watch_extra_costs ADD COLUMN category_id VARCHAR(32)"),
            ("basis_cents", "ALTER TABLE watch_extra_costs ADD COLUMN basis_cents INTEGER"),
            ("percent_milli", "ALTER TABLE watch_extra_costs ADD COLUMN percent_milli INTEGER"),
        ):
            if col not in cost_cols:
                try:
                    conn.execute(text(ddl))
                except Exception:  # noqa: BLE001
                    continue

        # ---- V4: Zustand/Versand als Schlüssel + Box-Verkaufs-Spalten ----
        watch_cols = {c["name"] for c in inspector.get_columns("watches")}
        for col, ddl in (
            ("condition_note", "ALTER TABLE watches ADD COLUMN condition_note VARCHAR(120)"),
            ("box_type_id", "ALTER TABLE watches ADD COLUMN box_type_id VARCHAR(32)"),
            ("box_unit_cost_cents", "ALTER TABLE watches ADD COLUMN box_unit_cost_cents INTEGER"),
            ("box_vk_share_cents", "ALTER TABLE watches ADD COLUMN box_vk_share_cents INTEGER"),
        ):
            if col not in watch_cols:
                with contextlib.suppress(Exception):
                    conn.execute(text(ddl))

        box_cols = {c["name"] for c in inspector.get_columns("box_purchases")}
        for col, ddl in (
            (
                "shipping_cents",
                "ALTER TABLE box_purchases ADD COLUMN shipping_cents INTEGER DEFAULT 0",
            ),
            ("currency", "ALTER TABLE box_purchases ADD COLUMN currency VARCHAR(3) DEFAULT 'EUR'"),
            ("rate_milli", "ALTER TABLE box_purchases ADD COLUMN rate_milli INTEGER"),
        ):
            if col not in box_cols:
                with contextlib.suppress(Exception):
                    conn.execute(text(ddl))

        # Alte Freitexte den Übersetzungsschlüsseln zuordnen (idempotent:
        # bereits zugeordnete Schlüssel werden nicht angefasst; unklare
        # Alttexte wandern intern in condition_note/internal_notes – nichts
        # geht verloren, Anzeigen erfinden nichts):
        from .texts import (
            is_condition_key,
            is_shipping_key,
            normalize_condition,
            normalize_shipping,
        )

        rows = conn.execute(
            text("SELECT id, condition, shipping_info, internal_notes FROM watches")
        ).fetchall()
        for wid, cond, ship, notes in rows:
            updates: dict = {}
            if cond and not is_condition_key(cond):
                key = normalize_condition(cond)
                if key:
                    updates["condition"] = key
                else:
                    updates["condition"] = None
                    updates["condition_note"] = cond[:120]
            if ship and not is_shipping_key(ship):
                key = normalize_shipping(ship)
                if key:
                    updates["shipping_info"] = key
                else:
                    updates["shipping_info"] = None
                    old_note = f"Alt-Versand: {ship[:160]}"
                    updates["internal_notes"] = (
                        f"{notes}\n{old_note}" if notes else old_note
                    )
            if updates:
                sets = ", ".join(f"{k} = :{k}" for k in updates)
                params = {"id": wid, **updates}
                conn.execute(
                    text(f"UPDATE watches SET {sets} WHERE id = :id"), params
                )

        # Startbestand der Kategorien (einmalig):
        if not conn.execute(text("SELECT 1 FROM cost_categories LIMIT 1")).first():
            for i, label in enumerate(DEFAULT_COST_CATEGORIES):
                conn.execute(
                    text(
                        "INSERT INTO cost_categories (id, label, sort_order, created_at) "
                        "VALUES (:id, :label, :sort, CURRENT_TIMESTAMP)"
                    ),
                    {"id": uuid.uuid4().hex, "label": label, "sort": i},
                )

        # Altbestand: Enum-Werte → passende Kategorie-ID (Label-Mapping),
        # nur solange die alte Spalte existiert (idempotent):
        if "category" in cost_cols:
            try:
                conn.execute(
                    text(
                        """UPDATE watch_extra_costs SET category_id = (
                             SELECT c.id FROM cost_categories c
                             WHERE c.label = CASE watch_extra_costs.category
                               WHEN 'china_logistics' THEN 'Versand China'
                               WHEN 'de_dhl' THEN 'Versand DHL (DE)'
                               WHEN 'wise' THEN 'Wise-Gebühren'
                               WHEN 'box' THEN 'Box'
                               ELSE 'Sonstiges'
                             END)
                           WHERE category_id IS NULL"""
                    )
                )
                conn.execute(text("ALTER TABLE watch_extra_costs DROP COLUMN category"))
            except Exception:  # noqa: BLE001 – Spalte bleibt ggf. liegen
                pass
