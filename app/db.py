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

    columns = {c["name"] for c in inspect(engine).get_columns("watches")}
    if "box_option" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE watches ADD COLUMN box_option VARCHAR(16)"))
