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


class BoxType(Base):
    """Box-Typ für das Box-Inventar (Bulk-Einkäufe, Durchschnitts-EK)."""

    __tablename__ = "box_types"
    __table_args__ = (UniqueConstraint("label", name="uq_box_type_label"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BoxPurchase(Base):
    """Bulk-Einkauf: `quantity` Boxen vom Typ für insgesamt `total_cost_cents`.

    Der EK pro Box ist der gewichtete Durchschnitt aller Einkäufe des Typs
    (bewusst kein FIFO – siehe Doku; Durchschnitt reicht bei Bulk-Bestellungen).
    """

    __tablename__ = "box_purchases"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_box_purchase_qty"),
        CheckConstraint("total_cost_cents >= 0", name="ck_box_purchase_total"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    box_type_id: Mapped[str] = mapped_column(
        ForeignKey("box_types.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    total_cost_cents: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(80))
    bought_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    box_type: Mapped[BoxType] = relationship()


# Startbestand der Kostenkategorien (einmalig in die DB gesät).
# Kategorien sind DATENBANK-pflegbar: der Nutzer kann auf /cost-categories
# selbst welche anlegen/umbenennen/archivieren – ohne Codeänderung.
DEFAULT_COST_CATEGORIES: list[str] = [
    "Versand China",
    "Versicherung",
    "Wise-Gebühren",
    "Versand DHL (DE)",
    "Box",
    "Import/Zoll",
    "Sonstiges",
]


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
            "target_price_cents IS NULL OR target_price_cents >= 0",
            name="ck_watch_target_price",
        ),
        CheckConstraint(
            "sale_price_cents IS NULL OR sale_price_cents >= 0",
            name="ck_watch_sale_price",
        ),
        CheckConstraint(
            "purchase_price_usd_cents IS NULL OR purchase_price_usd_cents >= 0",
            name="ck_watch_purchase_usd",
        ),
        CheckConstraint(
            "usd_eur_rate_milli IS NULL OR usd_eur_rate_milli > 0",
            name="ck_watch_usd_rate",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(160))
    # Titel-Bausteine (Titel setzt sich daraus zusammen, bleibt editierbar):
    factory: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[str | None] = mapped_column(String(40))
    band: Mapped[str | None] = mapped_column(String(80))
    highlight: Mapped[str | None] = mapped_column(String(160))

    status: Mapped[WatchStatus] = mapped_column(
        _sa_enum(WatchStatus, "watch_status"), default=WatchStatus.IN_CHINA, index=True
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # Vertraulich – niemals in öffentliche Routen oder Anzeigen übernehmen:
    # `purchase_price_cents` (EUR) ist die maßgebliche Grundlage für Marge;
    # bei USD-Eingabe wird es aus USD × Kurs abgeleitet (historischer Kurs).
    purchase_price_cents: Mapped[int | None] = mapped_column(Integer)
    purchase_price_usd_cents: Mapped[int | None] = mapped_column(Integer)
    usd_eur_rate_milli: Mapped[int | None] = mapped_column(Integer)  # EUR pro USD × 1000
    internal_notes: Mapped[str | None] = mapped_column(Text)

    target_price_cents: Mapped[int | None] = mapped_column(Integer)
    sale_price_cents: Mapped[int | None] = mapped_column(Integer)

    condition: Mapped[str | None] = mapped_column(String(80))
    # none | box | optional – NULL = nicht angegeben (wird nie geraten).
    box_option: Mapped[str | None] = mapped_column(String(16))
    shipping_info: Mapped[str | None] = mapped_column(Text)
    # Zustand/Notiz: condition speichert künftig einen Schlüssel aus texts.py;
    # condition_note ist interne Zusatznotiz und fließt NIE in Anzeigen.
    condition_note: Mapped[str | None] = mapped_column(String(120))
    # Verkauf mit Box (Inventar): Snapshots beim Setzen auf „Verkauft“.
    box_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("box_types.id", ondelete="SET NULL"), index=True
    )
    box_unit_cost_cents: Mapped[int | None] = mapped_column(Integer)
    box_vk_share_cents: Mapped[int | None] = mapped_column(Integer)
    box_type: Mapped[BoxType | None] = relationship()
    # Ausschließlich für autorisierte Wecatalog-Übernahmen (nicht im
    # Watch-Formular gepflegt; eigene Beschreibungen entfallen bewusst).
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
    extra_costs: Mapped[list[WatchExtraCost]] = relationship(
        back_populates="watch",
        cascade="all, delete-orphan",
        order_by="WatchExtraCost.id",
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


class CostCategory(Base):
    """Kostenkategorie (Daten) – vom Nutzer über die UI pflegbar.

    Dashboards gruppieren über die Kategorien-IDs; Umbenennen wirkt dann
    über alle Positionen, Archivieren verhindert Neunutzung ohne Datenverlust.
    """

    __tablename__ = "cost_categories"
    __table_args__ = (UniqueConstraint("label", name="uq_cost_category_label"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    label: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WatchExtraCost(Base):
    """Einzelposition zusätzlicher Einkaufskosten – Marge-Grundlage.

    Währung pro Position (USD/EUR); USD-Beträge werden mit dem historischen
    USD→EUR-Kurs der Uhr umgerechnet. Optional Prozent-Modus: dann sind
    `basis_cents` + `percent_milli` gesetzt und `amount_cents` ist der
    serverseitig berechnete Betrag in der Währung der Position.
    """

    __tablename__ = "watch_extra_costs"
    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_extra_cost_amount"),
        CheckConstraint("basis_cents IS NULL OR basis_cents >= 0", name="ck_extra_cost_basis"),
        CheckConstraint(
            "percent_milli IS NULL OR (percent_milli > 0 AND percent_milli <= 100000)",
            name="ck_extra_cost_percent",
        ),
        CheckConstraint(
            "currency IN ('USD','EUR')", name="ck_extra_cost_currency"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_id: Mapped[str] = mapped_column(
        ForeignKey("watches.id", ondelete="CASCADE"), index=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("cost_categories.id", ondelete="RESTRICT"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    amount_cents: Mapped[int] = mapped_column(Integer)
    basis_cents: Mapped[int | None] = mapped_column(Integer)
    percent_milli: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    watch: Mapped[Watch] = relationship(back_populates="extra_costs")
    category: Mapped[CostCategory] = relationship()

    @property
    def category_label(self) -> str:
        """Kategorie-Label mit sicherem Fallback (zeilentolerant)."""
        return self.category.label if self.category is not None else "—"


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
