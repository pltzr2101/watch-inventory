"""Datenbank-Anbindung (SQLite über SQLAlchemy 2.0)."""
from __future__ import annotations

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
    from sqlalchemy import inspect, text

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

    if statements:
        with engine.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception:  # noqa: BLE001 – z. B. alte SQLite ohne DROP COLUMN
                    continue
