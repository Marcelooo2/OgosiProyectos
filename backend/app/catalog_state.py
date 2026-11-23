"""Catalog lifecycle. Archived rows keep their original IDs and historic references."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CatalogBase(DeclarativeBase):
    pass


class CatalogArchive(CatalogBase):
    __tablename__ = "catalog_archives"
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)


def is_archived(db, kind, item_id):
    return db.get(CatalogArchive, (kind, item_id)) is not None


def active_rows(model, kind):
    archived = select(CatalogArchive.item_id).where(CatalogArchive.kind == kind)
    return select(model).where(model.id.not_in(archived))


class CatalogRevision(CatalogBase):
    __tablename__ = "catalog_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    item_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ResourceDetails(CatalogBase):
    __tablename__ = "resource_details"
    resource_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    entry_level: Mapped[int] = mapped_column(default=0)
    hours: Mapped[float | None] = mapped_column(Float)
    modality: Mapped[str] = mapped_column(String(40), default="En línea")
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)


def catalog_version(db):
    last = db.scalar(select(CatalogRevision.id).order_by(CatalogRevision.id.desc()).limit(1))
    return (last or 0) + 1


def active_parameters(db):
    last = db.scalar(select(CatalogRevision).where(CatalogRevision.kind == "parameters")
                     .order_by(CatalogRevision.id.desc()).limit(1))
    return last.after if last else {"semantic_threshold": 0.92}

