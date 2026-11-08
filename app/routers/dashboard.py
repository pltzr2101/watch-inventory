"""Übersichtsseite (Dashboard) mit Bestandszahlen und letztem Aktivitätslog."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from ..deps import DbDep, SettingsDep
from ..models import ImportPreview, StatusEvent, Watch, WatchStatus
from ..templating import render

router = APIRouter(tags=["dashboard"])


@router.get("/")
def dashboard(request: Request, db=DbDep, settings=SettingsDep):
    counts = dict(
        db.execute(
            select(Watch.status, func.count(Watch.id))
            .where(Watch.archived_at.is_(None))
            .group_by(Watch.status)
        ).all()
    )
    active_total = sum(counts.values())
    archived_total = db.scalar(
        select(func.count(Watch.id)).where(Watch.archived_at.is_not(None))
    ) or 0

    pending_imports = 0
    if settings.wecatalog_enabled:
        pending_imports = db.scalar(
            select(func.count(ImportPreview.id)).where(ImportPreview.status == "pending")
        ) or 0

    recent = db.scalars(
        select(StatusEvent)
        .order_by(StatusEvent.changed_at.desc())
        .limit(8)
    ).all()

    return render(
        request,
        "dashboard.html",
        counts={status: counts.get(status, 0) for status in WatchStatus},
        active_total=active_total,
        archived_total=archived_total,
        pending_imports=pending_imports,
        recent=recent,
        wecatalog_enabled=settings.wecatalog_enabled,
    )
