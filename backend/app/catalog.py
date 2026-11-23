"""Validation, reversible administration, and ranked learning resources."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, field_validator, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .catalog_state import (CatalogArchive, CatalogRevision, ResourceDetails, active_parameters,
                            active_rows, catalog_version)

Kind = Literal["competencies", "profiles", "resources"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ResourceTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class CatalogInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class SkillInput(CatalogInput):
    name: Name
    category: Literal["Técnica", "Blanda"]
    aliases: list[Name] = Field(default_factory=list, max_length=30)

    @field_validator("aliases")
    @classmethod
    def distinct_aliases(cls, values):
        return list({v.casefold(): v for v in values}.values())


class RequirementInput(CatalogInput):
    competency_id: int = Field(gt=0)
    required_level: int = Field(ge=1, le=5)
    weight: int = Field(ge=1, le=3)


class ProfileInput(CatalogInput):
    name: Name
    description: Description
    seniority: Name = Field(max_length=40)
    requirements: list[RequirementInput] = Field(min_length=1, max_length=100)

    @field_validator("requirements")
    @classmethod
    def distinct_skills(cls, items):
        if len({r.competency_id for r in items}) != len(items):
            raise ValueError("Cada competencia debe aparecer una vez por perfil")
        return items


class ResourceInput(CatalogInput):
    competency_id: int = Field(gt=0)
    title: ResourceTitle
    provider: Name = Field(max_length=100)
    resource_type: Name = Field(max_length=40)
    url: HttpUrl
    entry_level: int = Field(0, ge=0, le=5)
    hours: float | None = Field(None, gt=0, le=10000)
    modality: Literal["En línea", "Presencial", "Híbrida"] = "En línea"
    starts_on: date | None = None
    ends_on: date | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            raise ValueError("La fecha final debe ser igual o posterior a la fecha de inicio")
        return self

    @field_validator("url")
    @classmethod
    def public_link(cls, value):
        if value.scheme != "https" or value.username or value.password or len(str(value)) > 500:
            raise ValueError("Introduce una URL HTTPS sin credenciales, de hasta 500 caracteres")
        return value


class ArchiveInput(CatalogInput):
    archived: bool


class ModelParameters(CatalogInput):
    semantic_threshold: float = Field(ge=0, le=1)


LEARNING_MATERIALS = {
    "Comunicación": (
        "Technical Writing One", "Google for Developers",
        "https://developers.google.com/tech-writing/one", "Curso",
    ),
    "Trabajo en equipo": (
        "Working Agreements", "Atlassian Team Playbook",
        "https://www.atlassian.com/team-playbook/plays/working-agreements", "Taller",
    ),
    "Resolución de problemas": (
        "Effective Troubleshooting", "Google SRE",
        "https://sre.google/sre-book/effective-troubleshooting/", "Lectura",
    ),
}


def repair_seed_links(db, resource_model):
    # Migrate only known demo entries. Repeated application keeps user edits intact.
    legacy = [
        ("comunicacion", "Comunicación"),
        ("equipos", "Trabajo en equipo"),
        ("problemas", "Resolución de problemas"),
    ]
    for slug, name in legacy:
        title, provider, url, resource_type = LEARNING_MATERIALS[name]
        db.execute(update(resource_model).where(
            resource_model.url == "https://example.com/" + slug, resource_model.provider == "SkillGrow",
        ).values(title=title, provider=provider, url=url, resource_type=resource_type))
    db.execute(update(resource_model).where(
        resource_model.url == "https://docs.python.org/es/3/tutorial/", resource_model.provider == "OpenEDU",
    ).values(provider="Python Software Foundation"))
    db.commit()


def _commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Ya existe una entrada con esos datos") from exc


def _require(db, model, item_id):
    item = db.get(model, item_id)
    if not item:
        raise HTTPException(404, "Entrada de catálogo no encontrada")
    return item


def _name_available(db, model, name, current):
    stmt = select(model.id).where(func.lower(model.name) == name.lower())
    if current is not None:
        stmt = stmt.where(model.id != current.id)
    if db.scalar(stmt) is not None:
        raise HTTPException(409, "Ese nombre ya existe en el catálogo")


def profile_dict(item):
    return {
        "id": item.id, "name": item.name, "description": item.description, "seniority": item.seniority,
        "requirements": [
            {"competency_id": req.competency_id, "name": req.competency.name,
             "required_level": req.required_level, "weight": req.weight}
            for req in sorted(item.requirements, key=lambda r: r.id)
        ],
    }


def _details(db, item_id):
    row = db.get(ResourceDetails, item_id)
    return {key: getattr(row, key, default) for key, default in (
        ("entry_level", 0), ("hours", None), ("modality", "En línea"), ("starts_on", None), ("ends_on", None))}


def _serialize(kind, item, db):
    if kind == "profiles":
        return profile_dict(item)
    fields = ("name", "category", "aliases") if kind == "competencies" else (
        "title", "provider", "url", "resource_type", "competency_id",
    )
    data = {"id": item.id, **{key: getattr(item, key) for key in fields}}
    if kind == "resources":
        data.update({key: val.isoformat() if isinstance(val, date) else val
                     for key, val in _details(db, item.id).items()})
    return data


def register_catalog(app):
    from .main import (
        Assessment, Competency, CurrentUser, Db, JobProfile, JobRequirement, LearningResource,
        AdminProfileCreate, Progress, User, require_admin,
    )

    admin = APIRouter(prefix="/api/admin/catalog", dependencies=[Depends(require_admin)])
    models = {"profiles": JobProfile, "competencies": Competency, "resources": LearningResource}

    def check_skills(db, ids):
        values = set(db.scalars(active_rows(Competency, "competencies").where(Competency.id.in_(ids))).all())
        if len(values) != len(set(ids)):
            raise HTTPException(422, "Selecciona competencias activas del catálogo")

    @admin.get("")
    def catalogs(db: Db):
        archived = {(r.kind, r.item_id) for r in db.scalars(select(CatalogArchive))}
        return {"version": catalog_version(db), "parameters": active_parameters(db),
                **{kind: [dict(_serialize(kind, r, db), archived=(kind, r.id) in archived)
                       for r in db.scalars(select(model).order_by(model.id))]
                   for kind, model in models.items()}}

    def revision(db, user, kind, item_id, before, after, action):
        db.add(CatalogRevision(user_id=user.id, kind=kind, item_id=item_id, before=before,
                               after=after, action=action))
        _commit(db)
        return {"id": item_id, "version": catalog_version(db)}

    def publish(db, user, kind, item, before):
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "Ya existe una entrada con esos datos") from exc
        return revision(db, user, kind, item.id, before, _serialize(kind, item, db), "editar" if before else "crear")

    @admin.get("/history")
    def history(db: Db, before_version: int | None = None):
        stmt = select(CatalogRevision).order_by(CatalogRevision.id.desc())
        if before_version is not None:
            stmt = stmt.where(CatalogRevision.id < before_version - 1)
        rows = db.scalars(stmt.limit(50))
        return [{"version": row.id + 1, "kind": row.kind, "item_id": row.item_id, "user_id": row.user_id,
                 "date": row.published_at, "action": row.action, "before": row.before, "after": row.after} for row in rows]

    @admin.put("/parameters")
    def set_parameters(payload: ModelParameters, db: Db, user: CurrentUser):
        return revision(db, user, "parameters", 1, active_parameters(db), payload.model_dump(), "editar")

    def save_skill(payload, db, user, item=None):
        _name_available(db, Competency, payload.name, item)
        before = _serialize("competencies", item, db) if item else None
        item = item or Competency()
        item.name, item.category, item.aliases = payload.name, payload.category, payload.aliases
        db.add(item)
        return publish(db, user, "competencies", item, before)

    @admin.post("/competencies", status_code=201)
    def add_skill(payload: SkillInput, db: Db, user: CurrentUser):
        return save_skill(payload, db, user)

    @admin.put("/competencies/{item_id}")
    def edit_skill(item_id: int, payload: SkillInput, db: Db, user: CurrentUser):
        return save_skill(payload, db, user, _require(db, Competency, item_id))

    def save_profile(payload, db, user, item=None):
        _name_available(db, JobProfile, payload.name, item)
        before = _serialize("profiles", item, db) if item else None
        check_skills(db, [r.competency_id for r in payload.requirements])
        item = item or JobProfile()
        item.name = payload.name
        item.description = payload.description
        item.seniority = payload.seniority
        item.requirements = [JobRequirement(**req.model_dump()) for req in payload.requirements]
        db.add(item)
        return publish(db, user, "profiles", item, before)

    @admin.post("/profiles", status_code=201)
    def add_profile(payload: ProfileInput, db: Db, user: CurrentUser):
        return save_profile(payload, db, user)

    @admin.put("/profiles/{item_id}")
    def edit_profile(item_id: int, payload: ProfileInput, db: Db, user: CurrentUser):
        return save_profile(payload, db, user, _require(db, JobProfile, item_id))

    def save_resource(payload, db, user, item=None):
        check_skills(db, [payload.competency_id])
        before = _serialize("resources", item, db) if item else None
        duplicates = select(LearningResource.id).where(LearningResource.competency_id == payload.competency_id,
                                                       LearningResource.url == str(payload.url))
        if item:
            duplicates = duplicates.where(LearningResource.id != item.id)
        if db.scalar(duplicates) is not None:
            raise HTTPException(409, "Ese enlace ya figura en los recursos de la competencia")
        item = item or LearningResource()
        detail_fields = {"entry_level", "hours", "modality", "starts_on", "ends_on"}
        data = payload.model_dump(mode="json", exclude=detail_fields)
        for name, value in data.items():
            setattr(item, name, value)
        db.add(item)
        db.flush()
        details = db.get(ResourceDetails, item.id) or ResourceDetails(resource_id=item.id)
        for key in detail_fields:
            setattr(details, key, getattr(payload, key))
        db.add(details)
        return publish(db, user, "resources", item, before)

    @admin.post("/resources", status_code=201)
    def add_resource(payload: ResourceInput, db: Db, user: CurrentUser):
        return save_resource(payload, db, user)

    @admin.put("/resources/{item_id}")
    def edit_resource(item_id: int, payload: ResourceInput, db: Db, user: CurrentUser):
        return save_resource(payload, db, user, _require(db, LearningResource, item_id))

    @admin.patch("/{kind}/{item_id}/status")
    def change_status(kind: Kind, item_id: int, payload: ArchiveInput, db: Db, user: CurrentUser):
        item = _require(db, models[kind], item_id)
        previous = db.get(CatalogArchive, (kind, item_id))
        if payload.archived and not previous:
            if kind == "competencies":
                used = active_rows(JobProfile, "profiles").join(JobRequirement).where(
                    JobRequirement.competency_id == item_id)
                linked = active_rows(LearningResource, "resources").where(
                    LearningResource.competency_id == item_id)
                if db.scalar(used.limit(1)) or db.scalar(linked.limit(1)):
                    raise HTTPException(409, "Primero reasigna o archiva los perfiles y recursos que usan esta competencia")
            db.add(CatalogArchive(kind=kind, item_id=item_id))
        elif not payload.archived and previous:
            if kind == "profiles":
                check_skills(db, [r.competency_id for r in item.requirements])
            if kind == "resources":
                check_skills(db, [item.competency_id])
            db.delete(previous)
        else:
            return {"id": item_id, "archived": payload.archived}
        change = "archivar" if payload.archived else "restaurar"
        revision(db, user, kind, item_id, {"archived": bool(previous)}, {"archived": payload.archived}, change)
        return {"id": item_id, "archived": payload.archived}

    app.include_router(admin)

    @app.post("/api/admin/profiles", status_code=201, dependencies=[Depends(require_admin)])
    def create_legacy_profile(payload: AdminProfileCreate, db: Db, user: CurrentUser):
        # Older clients submit a mapping from a skill name to its required level.
        reqs = []
        for name, level in payload.requirements.items():
            skill = db.scalar(select(Competency).where(func.lower(Competency.name) == name.strip().lower()))
            if skill is None:
                skill = Competency(name=name.strip(), category="Técnica", aliases=[])
                db.add(skill)
                db.flush()
                db.add(CatalogRevision(user_id=user.id, kind="competencies", item_id=skill.id, before=None,
                                       after=_serialize("competencies", skill, db), action="crear"))
            reqs.append(RequirementInput(competency_id=skill.id, required_level=int(level), weight=1))
        converted = ProfileInput(name=payload.name, description=payload.description,
                                 seniority=payload.seniority, requirements=reqs)
        return save_profile(converted, db, user)

    def card(res, progress=None):
        return {
            "progress_id": progress.id if progress else None, "resource_id": res.id,
            "title": res.title, "provider": res.provider,
            "url": res.url, "type": res.resource_type, "competency": res.competency.name,
            "percent": progress.percent if progress else 0, "completed": bool(progress and progress.completed),
        }

    def suggestions(db, user, modality=None):
        latest = db.scalar(select(Assessment).where(Assessment.user_id == user.id)
                           .order_by(Assessment.created_at.desc(), Assessment.id.desc()).limit(1))
        if not latest:
            raise HTTPException(404, "Completa un diagnóstico para obtener recomendaciones")
        gaps = {s["competency_id"]: s for s in latest.scores if (s["gap"] or 0) > 0}
        resources = db.scalars(active_rows(LearningResource, "resources")
                               .where(LearningResource.competency_id.in_(gaps)))
        today = date.today()
        prev = db.scalars(select(Progress).where(Progress.user_id == user.id)
                          .order_by(Progress.percent, Progress.id.desc())).all()
        by_resource = {p.resource_id: p for p in prev}
        result = []
        version = catalog_version(db)
        for resource in resources:
            info = _details(db, resource.id)
            score = gaps[resource.competency_id]
            if (info["starts_on"] and info["starts_on"] > today) or (info["ends_on"] and info["ends_on"] < today):
                continue
            if (modality and modality != info["modality"]) or info["entry_level"] > (score.get("evidence") or 0) / 20:
                continue
            priority = score.get("priority", score["gap"] * score.get("weight", 1) / 100)
            result.append({**card(resource, by_resource.get(resource.id)), **info, "catalog_version": version,
                           "priority": priority, "gap": score["gap"], "archived": False})
        result.sort(key=lambda r: (-r["priority"], r["hours"] or float("inf"), r["title"].casefold(), r["resource_id"]))
        return result

    @app.get("/api/route/suggestions")
    def route_suggestions(db: Db, user: CurrentUser, modality: str | None = None):
        return suggestions(db, user, modality)[:3]

    def enroll(db, user, resource_id):
        progress = db.scalar(select(Progress).where(Progress.user_id == user.id, Progress.resource_id == resource_id)
                             .order_by(Progress.percent.desc(), Progress.id).limit(1))
        if progress is None:
            progress = Progress(user_id=user.id, resource_id=resource_id)
            db.add(progress)
            db.flush()
        return progress

    @app.post("/api/route/enroll/{resource_id}")
    def add_to_route(resource_id: int, db: Db, user: CurrentUser):
        db.execute(update(User).where(User.id == user.id).values(consent=User.consent))
        if resource_id not in {r["resource_id"] for r in suggestions(db, user)}:
            raise HTTPException(409, "El recurso ya no coincide con el diagnóstico o el catálogo vigente")
        progress = enroll(db, user, resource_id)
        db.commit()
        return card(progress.resource, progress)

    @app.get("/api/recommendations")
    def recommendations(db: Db, user: CurrentUser):
        # Preserve automatic assignment for clients using the original endpoint.
        db.execute(update(User).where(User.id == user.id).values(consent=User.consent))
        result = suggestions(db, user)[:3]
        for entry in result:
            resource_id = entry["resource_id"]
            progress = enroll(db, user, resource_id)
            entry.update(progress_id=progress.id, percent=progress.percent, completed=progress.completed)
        db.commit()
        return result

    @app.get("/api/progress")
    def list_progress(db: Db, user: CurrentUser):
        archived = set(db.scalars(select(CatalogArchive.item_id).where(CatalogArchive.kind == "resources")))
        rows = db.scalars(select(Progress).where(Progress.user_id == user.id)
                          .options(selectinload(Progress.resource).selectinload(LearningResource.competency))
                          .order_by(Progress.id))
        return [dict(card(p.resource, p), archived=p.resource_id in archived) for p in rows]
