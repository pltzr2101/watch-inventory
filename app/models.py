"""ORM-Datenmodelle.

Architekturentscheidung für spätere Funktionen (4 & 5):
- Vertrauliche Daten (Einkaufspreise, interne Notizen, Quell-Snapshots) liegen
  in eigenen Spalten, die später in einer öffentlichen read-only Ansicht schlicht
  nie ausgewählt werden. `public_approved` ist die spätere Freigabe-Schwelle;
  aktuell existiert keine öffentliche Route, die es anwendet.
- Statuswerte sind strukturiert (u. a. in_china / in_germany) für die spätere
  Trennung „In Deutschland" / „In China".
- `created_at`, `sold_at`, `status_history` und `imported_at` liefern die
  Zeitstempel für spätere Lagerdauer-/Verkaufsanalysen.
"""
from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Aktuelle UTC-Zeit (naiv gespeichert, SQLite-kompatibel; immer UTC)."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


class WatchStatus(StrEnum):
    IN_CHINA = "in_china"
    IN_TRANSIT = "in_transit"
    IN_GERMANY = "in_germany"
    RESERVED = "reserved"
    SOLD = "sold"

    @property
    def label(self) -> str:
        return {
            "in_china": "Beim Lieferanten / in China",
            "in_transit": "Unterwegs",
            "in_germany": "In Deutschland / verfügbar",
            "reserved": "Reserviert",
            "sold": "Verkauft",
        }[self.value]


class Provenance(StrEnum):
    """Datenherkunft eines Uhrendatensatzes."""

    MANUAL = "manual"  # manuell eingegeben
    OWN = "own"  # eigene Inhalte
    WECATALOG = "wecatalog"  # autorisiert aus Wecatalog.cn importiert


class ImageSource(StrEnum):
    OWNER = "owner"  # eigene Fotos
    WECATALOG = "wecatalog"  # aus dem Katalog übernommen (unverändert behandeln)


class ListingPlatform(StrEnum):
    REDDIT = "reddit"
    REDDIT_CODE = "reddit_code"
    FACEBOOK = "facebook"
    TELEGRAM = "telegram"


class PublicationStatus(StrEnum):
    """Veröffentlichungs-Status je Uhr × Plattform (Skelett für späteres Posten)."""

    DRAFT = "draft"
    READY = "ready"
    POSTED = "posted"

    @property
    def label(self) -> str:
        return {"draft": "Entwurf", "ready": "Bereit", "posted": "Gepostet"}[self.value]


# Box-Option (Lieferumfang-Teil): None = nicht angegeben (keine Aussage!).
BOX_OPTIONS: dict[str, str] = {
    "none": "Ohne Box",
    "box": "Box inklusive",
    "optional": "Box optional (+50 €)",
}


def _sa_enum(enum_cls: type[enum.Enum], name: str):  # type: ignore[no-untyped-def]
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class Watch(Base):
    __tablename__ = "watches"
    __table_args__ = (
        CheckConstraint(
            "purchase_price_cents IS NULL OR purchase_price_cents >= 0",
            name="ck_watch_purchase_price",
        ),
        CheckConstraint(
            "extra_costs_cents IS NULL OR extra_costs_cents >= 0",
            name="ck_watch_extra_costs",
        ),
        CheckConstraint(
            "target_price_cents IS NULL OR target_price_cents >= 0",
            name="ck_watch_target_price",
        ),
        CheckConstraint(
            "sale_price_cents IS NULL OR sale_price_cents >= 0",
            name="ck_watch_sale_price",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(160))

    status: Mapped[WatchStatus] = mapped_column(
        _sa_enum(WatchStatus, "watch_status"), default=WatchStatus.IN_CHINA, index=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Vertraulich – niemals in öffentliche Routen oder Anzeigen übernehmen:
    purchase_price_cents: Mapped[int | None] = mapped_column(Integer)
    extra_costs_cents: Mapped[int | None] = mapped_column(Integer)
    extra_costs_note: Mapped[str | None] = mapped_column(Text)
    internal_notes: Mapped[str | None] = mapped_column(Text)

    target_price_cents: Mapped[int | None] = mapped_column(Integer)
    sale_price_cents: Mapped[int | None] = mapped_column(Integer)

    condition: Mapped[str | None] = mapped_column(String(80))
    condition_description: Mapped[str | None] = mapped_column(Text)
    included_accessories: Mapped[str | None] = mapped_column(Text)
    # none | box | optional – NULL = nicht angegeben (wird nie geraten).
    box_option: Mapped[str | None] = mapped_column(String(16))
    shipping_info: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    specs: Mapped[list] = mapped_column(JSON, default=list)

    @property
    def box_label(self) -> str:
        """Deutsche Box-Angabe für die Detailansicht („—" wenn nicht angegeben)."""
        if not self.box_option:
            return "—"
        return BOX_OPTIONS.get(self.box_option, "—")

    # Importierte Quelldaten (Wecatalog.cn) – getrennt von eigenen Inhalten:
    provenance: Mapped[Provenance] = mapped_column(
        _sa_enum(Provenance, "provenance"), default=Provenance.MANUAL
    )
    source_name: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(500))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_content: Mapped[dict | None] = mapped_column(JSON)
    source_fields: Mapped[list] = mapped_column(JSON, default=list)
    import_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    import_approved_for_listing: Mapped[bool] = mapped_column(Boolean, default=False)

    # Spätere öffentliche Freigabe – aktiviert KEINE öffentliche Auslieferung:
    public_approved: Mapped[bool] = mapped_column(Boolean, default=False)

    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime)

    images: Mapped[list[WatchImage]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        order_by="WatchImage.sort_order",
    )
    status_events: Mapped[list[StatusEvent]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        order_by="StatusEvent.changed_at",
    )
    publications: Mapped[list[Publication]] = relationship(
        back_populates="watch", cascade="all, delete-orphan"
    )

    listing_drafts: Mapped[list[ListingDraft]] = relationship(
        back_populates="watch", cascade="all, delete-orphan"
    )


class StatusEvent(Base):
    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[WatchStatus | None] = mapped_column(
        _sa_enum(WatchStatus, "watch_status_from")
    )
    to_status: Mapped[WatchStatus] = mapped_column(_sa_enum(WatchStatus, "watch_status_to"))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str | None] = mapped_column(String(300))

    watch: Mapped[Watch] = relationship(back_populates="status_events")


class WatchImage(Base):
    __tablename__ = "watch_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    original_name: Mapped[str] = mapped_column(String(200))
    source: Mapped[ImageSource] = mapped_column(
        _sa_enum(ImageSource, "image_source"), default=ImageSource.OWNER
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str | None] = mapped_column(String(60))
    mime: Mapped[str] = mapped_column(String(40))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    adjustments: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    watch: Mapped[Watch] = relationship(back_populates="images")


class ListingDraft(Base):
    __tablename__ = "listing_drafts"
    __table_args__ = (UniqueConstraint("watch_id", "platform", name="uq_listing_draft"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[ListingPlatform] = mapped_column(
        _sa_enum(ListingPlatform, "listing_platform")
    )
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    watch: Mapped[Watch] = relationship(back_populates="listing_drafts")


class Publication(Base):
    """Veröffentlichungs-Status je Uhr × Plattform (Grundgerüst: kein Auto-Posten).

    `posted_at`/`url` dokumentieren spätere Veröffentlichungen und bilden die
    Basis für spätere Verkaufs-/Kanal-Auswertungen; das eigentliche Posten
    erfolgt weiterhin manuell (Kanal-Automation ist bewusst offen/Später-Stufe).
    """

    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("watch_id", "platform", name="uq_publication"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[ListingPlatform] = mapped_column(
        _sa_enum(ListingPlatform, "listing_platform")
    )
    status: Mapped[str] = mapped_column(String(16), default=PublicationStatus.DRAFT.value)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    watch: Mapped[Watch] = relationship(back_populates="publications")


class ImportPreview(Base):
    """Importvorschlag aus Wecatalog.cn – erst nach Bestätigung wirksam."""

    __tablename__ = "import_previews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    watch_id: Mapped[str | None] = mapped_column(
        ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # "new" | "update"
    payload: Mapped[dict] = mapped_column(JSON)  # bereits preisbereinigt
    source_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/confirmed/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    pending_images: Mapped[list] = mapped_column(JSON, default=list)
