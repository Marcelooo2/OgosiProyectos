from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt

from .catalog import LEARNING_MATERIALS, profile_dict, register_catalog, repair_seed_links
from .catalog_state import CatalogBase, active_rows, catalog_version
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from . import cv, questionnaire


PRODUCTION = os.getenv("APP_ENV", "development").lower() == "production"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./skillgrow.db")
for legacy_scheme in ("postgresql://", "postgres://"):
    if DATABASE_URL.startswith(legacy_scheme):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(legacy_scheme):]
JWT_SECRET = os.getenv("JWT_SECRET") or ("" if PRODUCTION else secrets.token_urlsafe(48))
if PRODUCTION and (len(JWT_SECRET) < 32 or JWT_SECRET in {"desarrollo-local-cambiar-en-produccion", "skillgrow-dev-secret-change-me"}):
    raise RuntimeError("Configure JWT_SECRET con un secreto aleatorio de al menos 32 caracteres.")
if PRODUCTION and not DATABASE_URL.startswith("postgresql+psycopg://"):
    raise RuntimeError("Configure DATABASE_URL con PostgreSQL para producción.")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:4200")
ALGORITHM = "HS256"
POLICY_VERSION = "2026-11-23"

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(30), default="applicant")
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Competency(Base):
    __tablename__ = "competencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(60), default="Técnica")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)


class ConsentEvent(Base):
    __tablename__ = "consent_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[str] = mapped_column(String(30))
    accepted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JobProfile(Base):
    __tablename__ = "job_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(140), unique=True)
    description: Mapped[str] = mapped_column(Text)
    seniority: Mapped[str] = mapped_column(String(40), default="Junior")
    requirements: Mapped[list[JobRequirement]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class JobRequirement(Base):
    __tablename__ = "job_requirements"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("job_profiles.id"))
    competency_id: Mapped[int] = mapped_column(ForeignKey("competencies.id"))
    required_level: Mapped[float] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    profile: Mapped[JobProfile] = relationship(back_populates="requirements")
    competency: Mapped[Competency] = relationship()


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    detected: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("job_profiles.id"))
    scores: Mapped[list[dict]] = mapped_column(JSON)
    overall_fit: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    profile: Mapped[JobProfile] = relationship()


class ProfileDraft(Base):
    __tablename__ = "profile_drafts"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("job_profiles.id"), primary_key=True)
    self_scores: Mapped[dict] = mapped_column(JSON)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("job_profiles.id"), index=True)
    answers: Mapped[dict] = mapped_column(JSON)
    scores: Mapped[dict] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LearningResource(Base):
    __tablename__ = "learning_resources"
    id: Mapped[int] = mapped_column(primary_key=True)
    competency_id: Mapped[int] = mapped_column(ForeignKey("competencies.id"))
    title: Mapped[str] = mapped_column(String(180))
    provider: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(500))
    resource_type: Mapped[str] = mapped_column(String(40), default="Curso")
    competency: Mapped[Competency] = relationship()


class Progress(Base):
    __tablename__ = "progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("learning_resources.id"))
    percent: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    resource: Mapped[LearningResource] = relationship()


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=8, max_length=128)
    consent: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    renew_consent: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    role: str
    consent: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


LevelPercent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class DraftRequest(BaseModel):
    self_scores: dict[str, LevelPercent] = Field(max_length=500)


class AssessmentRequest(BaseModel):
    profile_id: int
    self_scores: dict[str, LevelPercent] | None = Field(default=None, max_length=500)


class ConfirmRequest(BaseModel):
    confirmed_ids: set[int] = Field(max_length=500)


class QuizSubmission(BaseModel):
    version: str = Field(max_length=40)
    answers: dict[str, Annotated[int, Field(strict=True, ge=0, le=2)]] = Field(max_length=200)


class ProgressUpdate(BaseModel):
    percent: int = Field(ge=0, le=100)


class AdminProfileCreate(BaseModel):
    name: str
    description: str
    seniority: str = "Junior"
    requirements: dict[str, float]


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


Db = Annotated[Session, Depends(get_db)]


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = encoded.split("$", 1)
    actual = hash_password(password, salt).split("$", 1)[1]
    return secrets.compare_digest(actual, expected)


def latest_consent(db: Session, user_id: int):
    return db.scalar(select(ConsentEvent).where(ConsentEvent.user_id == user_id).order_by(ConsentEvent.id.desc()).limit(1))


def record_consent(db: Session, user: User, accepted: bool) -> None:
    user.consent = accepted
    db.add(ConsentEvent(user_id=user.id, version=POLICY_VERSION, accepted=accepted))
    db.commit()


def create_token(user: User, db: Session) -> str:
    event = latest_consent(db, user.id)
    payload = {"sub": str(user.id), "grant": event.id if event else 0, "exp": datetime.now(timezone.utc) + timedelta(hours=12)}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def current_user(token: Annotated[str, Depends(oauth2_scheme)], db: Db) -> User:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Sesión inválida o vencida") from exc
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    event = latest_consent(db, user.id)
    if not user.consent or (event and (not event.accepted or event.id != payload.get("grant") or event.version != POLICY_VERSION)):
        raise HTTPException(status_code=401, detail="El consentimiento o la sesión han cambiado. Vuelve a ingresar.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Se requiere rol administrador")
    return user


def latest_cv(db, user_id):
    return db.scalar(select(Evidence).where(Evidence.user_id == user_id, Evidence.source == "cv").order_by(Evidence.id.desc()).limit(1))


def latest_quiz(db, user_id, profile_id):
    return db.scalar(select(QuizAttempt).where(QuizAttempt.user_id == user_id, QuizAttempt.profile_id == profile_id,
                     QuizAttempt.version == questionnaire.BANK_VERSION).order_by(QuizAttempt.id.desc()).limit(1))


def active_profile(db, profile_id):
    from .catalog_state import is_archived
    profile = db.get(JobProfile, profile_id)
    if profile is None or is_archived(db, "profiles", profile_id):
        raise HTTPException(404, "Perfil laboral no disponible.")
    return profile


def store_draft(db, user_id, profile, values):
    from sqlalchemy.dialects.postgresql import insert as postgres_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    ids = {r.competency.name: str(r.competency_id) for r in profile.requirements}
    if not set(values).issubset(ids):
        raise HTTPException(422, "La autodeclaración incluye competencias fuera del perfil.")
    by_id = {ids[name]: value for name, value in values.items()}
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else postgres_insert
    record = insert(ProfileDraft).values(user_id=user_id, profile_id=profile.id, self_scores=by_id)
    db.execute(record.on_conflict_do_update(index_elements=["user_id", "profile_id"], set_={"self_scores": by_id}))
    return by_id


def show_assessment(item):
    result = {"id": item.id, "profile": item.profile.name, "profile_id": item.profile_id,
              "overall_fit": item.overall_fit, "scores": item.scores, "created_at": item.created_at}
    if item.scores and item.scores[0].get("method") == "5.5.2-v1":
        result.update(questionnaire.summarize(item.scores))
        result.update(method="5.5.2-v1", catalog_version=item.scores[0]["catalog_version"])
    else:
        result["method"] = "historico-40-60"
    return result


app = FastAPI(title="SkillGrow API", version="1.0.0", description="Diagnóstico y orientación de brechas competenciales")
development_origins = ({os.environ["FRONTEND_ORIGIN"]} if os.getenv("FRONTEND_ORIGIN") else set()) if PRODUCTION else {FRONTEND_ORIGIN, "http://localhost:4200", "http://127.0.0.1:4200"}
app.add_middleware(CORSMiddleware, allow_origins=sorted(development_origins), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health(db: Db):
    db.execute(select(1))
    return {"status": "ok", "service": "skillgrow-api"}


@app.post("/api/auth/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Db):
    if not payload.consent:
        raise HTTPException(status_code=422, detail="Debe aceptar el tratamiento de datos para continuar")
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="El correo ya está registrado")
    user = User(email=payload.email.lower(), full_name=payload.full_name.strip(), password_hash=hash_password(payload.password), consent=True)
    db.add(user)
    from sqlalchemy.exc import IntegrityError
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "El correo ya está registrado") from exc
    record_consent(db, user, True)
    return TokenOut(access_token=create_token(user, db), user=user)


@app.post("/api/auth/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Db):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    event = latest_consent(db, user.id)
    if not user.consent or (event and event.version != POLICY_VERSION):
        if not payload.renew_consent:
            raise HTTPException(status_code=403, detail="Para reactivar tu cuenta, marca la casilla de consentimiento e ingresa de nuevo.")
        record_consent(db, user, True)
    return TokenOut(access_token=create_token(user, db), user=user)


@app.post("/api/me/consent/revoke")
def revoke_consent(db: Db, user: CurrentUser):
    record_consent(db, user, False)
    return {"version": POLICY_VERSION, "accepted": False}


@app.get("/api/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@app.get("/api/profiles")
def list_profiles(db: Db, _: CurrentUser):
    version = catalog_version(db)
    return [dict(profile_dict(p), catalog_version=version)
            for p in db.scalars(active_rows(JobProfile, "profiles").order_by(JobProfile.id))]


@app.post("/api/evidence/cv")
def upload_cv(db: Db, user: CurrentUser, file: UploadFile = File(...)):
    from .catalog_state import active_parameters, active_rows
    data = file.file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, "El archivo supera el límite de 5 MB.")
    content = cv.readable_text(file.filename, data)
    competencies = db.scalars(active_rows(Competency, "competencies")).all()
    detected = cv.extract_competencies(content, competencies, db,
                                        threshold=active_parameters(db)["semantic_threshold"])
    evidence = Evidence(user_id=user.id, source="cv", filename=(file.filename or "")[:255], extracted_text="", detected=detected)
    db.add(evidence)
    db.commit()
    message = ("Selecciona y guarda las menciones que reconoces." if detected else
               "No se reconocieron competencias del catálogo. Puedes continuar con tu autodeclaración.")
    return {"evidence_id": evidence.id, "filename": evidence.filename, "detected": detected, "notice": message}


@app.get("/api/evidence/cv/latest")
def get_cv(db: Db, user: CurrentUser):
    item = latest_cv(db, user.id)
    return {"evidence_id": item.id if item else None, "filename": item.filename if item else None,
            "detected": item.detected if item else []}


@app.put("/api/evidence/cv/{evidence_id}/confirmation")
def confirm_cv(evidence_id: int, payload: ConfirmRequest, db: Db, user: CurrentUser):
    item = latest_cv(db, user.id)
    if not item or item.id != evidence_id:
        raise HTTPException(404, "El CV actual no coincide. Actualiza la página.")
    if payload.confirmed_ids - {v["competency_id"] for v in item.detected}:
        raise HTTPException(422, "Solo se pueden confirmar menciones identificadas en este CV.")
    item.detected = [dict(v, confirmed=v["competency_id"] in payload.confirmed_ids) for v in item.detected]
    db.commit()
    return {"evidence_id": item.id, "detected": item.detected}


@app.get("/api/profiles/{profile_id}/questionnaire")
def read_questionnaire(profile_id: int, db: Db, user: CurrentUser):
    profile = active_profile(db, profile_id)
    draft = db.get(ProfileDraft, (user.id, profile_id))
    attempt = latest_quiz(db, user.id, profile_id)
    questions = questionnaire.profile_questions(profile.requirements)
    available = {q_id for _, q_id in questions}
    return {"version": questionnaire.BANK_VERSION, "items": questionnaire.public_items(questions),
            "uncovered": [r.competency.name for r in profile.requirements if r.competency_id not in available],
            "self_scores": {r.competency.name: draft.self_scores[str(r.competency_id)] for r in profile.requirements
                            if draft and str(r.competency_id) in draft.self_scores},
            "latest": {"id": attempt.id, "created_at": attempt.created_at, "scores": attempt.scores} if attempt else None}


@app.put("/api/profiles/{profile_id}/declaration")
def save_declaration(profile_id: int, payload: DraftRequest, db: Db, user: CurrentUser):
    profile = active_profile(db, profile_id)
    store_draft(db, user.id, profile, payload.self_scores)
    db.commit()
    return {"saved": True}


@app.post("/api/profiles/{profile_id}/questionnaire", status_code=201)
def submit_questionnaire(profile_id: int, payload: QuizSubmission, db: Db, user: CurrentUser):
    profile = active_profile(db, profile_id)
    if payload.version != questionnaire.BANK_VERSION:
        raise HTTPException(409, "El cuestionario cambió. Vuelve a cargarlo.")
    questions = questionnaire.profile_questions(profile.requirements)
    try:
        scores = questionnaire.grade(questions, payload.answers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    attempt = QuizAttempt(user_id=user.id, profile_id=profile.id, answers=payload.answers, scores=scores, version=payload.version)
    db.add(attempt)
    db.commit()
    return {"id": attempt.id, "created_at": attempt.created_at, "scores": scores}


@app.post("/api/assessments")
def create_assessment(payload: AssessmentRequest, db: Db, user: CurrentUser):
    from .catalog_state import catalog_version
    profile = active_profile(db, payload.profile_id)
    if not profile.requirements:
        raise HTTPException(422, "El perfil no tiene competencias requeridas.")
    if payload.self_scores is not None:
        declared = store_draft(db, user.id, profile, payload.self_scores)
    else:
        draft = db.get(ProfileDraft, (user.id, profile.id))
        declared = draft.self_scores if draft else {}
    evidence = latest_cv(db, user.id)
    attempt = latest_quiz(db, user.id, profile.id)
    confirmed = {entry["competency_id"]: entry for entry in (evidence.detected if evidence else []) if entry.get("confirmed")}
    version = catalog_version(db)
    previous = db.scalar(select(Assessment).where(Assessment.user_id == user.id, Assessment.profile_id == profile.id)
                         .order_by(Assessment.id.desc()).limit(1))
    if previous and previous.scores and previous.scores[0].get("method") == "5.5.2-v1":
        prior = previous.scores[0]
        if prior.get("quiz_attempt_id") == (attempt.id if attempt else None) and prior["catalog_version"] == version:
            # An empty first report may be superseded by the initial declaration.
            was_evaluable = any(s["gap"] is not None for s in previous.scores)
            if was_evaluable:
                db.commit()
                return dict(show_assessment(previous), reused=True)
    scores = []
    for requirement in profile.requirements:
        key = str(requirement.competency_id)
        mention = confirmed.get(requirement.competency_id)
        sources = {"cv": 100 if mention else None, "self": declared.get(key),
                   "quiz": attempt.scores.get(key) if attempt else None}
        row = questionnaire.diagnostic_score(requirement, sources)
        row.update(catalog_version=version, quiz_attempt_id=attempt.id if attempt else None,
                   quiz_version=attempt.version if attempt else None,
                   verified_at=attempt.created_at.isoformat() if attempt and sources["quiz"] is not None else None,
                   cv_source=mention.get("source") if mention else None, cv_evidence_id=evidence.id if mention else None)
        scores.append(row)
    summary = questionnaire.summarize(scores)
    # Older databases have a NOT NULL overall_fit; the public result comes from the frozen score rows.
    assessment = Assessment(user_id=user.id, profile_id=profile.id, scores=scores, overall_fit=summary["overall_fit"] or 0)
    db.add(assessment)
    db.commit()
    return show_assessment(assessment)


@app.get("/api/assessments/latest")
def latest_assessment(db: Db, user: CurrentUser):
    item = db.scalar(select(Assessment).where(Assessment.user_id == user.id).order_by(Assessment.id.desc()).limit(1))
    if not item:
        raise HTTPException(404, "Aún no existe un diagnóstico.")
    return show_assessment(item)




@app.patch("/api/progress/{progress_id}")
def update_progress(progress_id: int, payload: ProgressUpdate, db: Db, user: CurrentUser):
    item = db.get(Progress, progress_id)
    if not item or item.user_id != user.id:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")
    item.percent = payload.percent
    item.completed = payload.percent == 100
    db.commit()
    return {"id": item.id, "percent": item.percent, "completed": item.completed, "notice": "El progreso formativo no modifica la brecha hasta realizar una nueva evaluación."}




def seed_data(db: Session) -> None:
    if db.scalar(select(Competency.id).limit(1)):
        return
    competency_data = [
        ("Python", "Técnica", ["python3"]), ("FastAPI", "Técnica", ["fast api"]),
        ("SQL", "Técnica", ["postgresql", "mysql", "sql server"]), ("Git", "Técnica", ["github", "gitlab"]),
        ("Angular", "Técnica", ["typescript", "angularjs"]), ("REST API", "Técnica", ["api rest", "restful"]),
        ("Comunicación", "Blanda", ["comunicación efectiva"]), ("Trabajo en equipo", "Blanda", ["colaboración"]),
        ("Resolución de problemas", "Blanda", ["problem solving"]), ("Docker", "Técnica", ["contenedores"]),
    ]
    competencies: dict[str, Competency] = {}
    for name, category, aliases in competency_data:
        item = Competency(name=name, category=category, aliases=aliases)
        db.add(item)
        competencies[name] = item
    db.flush()
    profiles = [
        ("Desarrollador Backend", "Construye APIs seguras, servicios de negocio y persistencia de datos.", "Junior", {"Python": 4, "FastAPI": 3, "SQL": 4, "Git": 3, "REST API": 4, "Comunicación": 3}),
        ("Desarrollador Frontend", "Crea experiencias web accesibles, responsivas y mantenibles.", "Junior", {"Angular": 4, "Git": 3, "REST API": 3, "Trabajo en equipo": 4, "Resolución de problemas": 3}),
        ("Desarrollador Full Stack", "Integra interfaces web, APIs y bases de datos de extremo a extremo.", "Junior", {"Angular": 3, "Python": 3, "SQL": 3, "Git": 3, "REST API": 4, "Docker": 2}),
    ]
    for name, description, seniority, requirements in profiles:
        profile = JobProfile(name=name, description=description, seniority=seniority)
        db.add(profile)
        for competency_name, level in requirements.items():
            profile.requirements.append(JobRequirement(competency=competencies[competency_name], required_level=level))
    resources = [
        ("Python", "Python desde cero", "Python Software Foundation", "https://docs.python.org/es/3/tutorial/", "Curso"),
        ("FastAPI", "API profesional con FastAPI", "FastAPI", "https://fastapi.tiangolo.com/tutorial/", "Proyecto guiado"),
        ("SQL", "Fundamentos de PostgreSQL", "PostgreSQL", "https://www.postgresql.org/docs/current/tutorial.html", "Curso"),
        ("Angular", "Tutorial oficial de Angular", "Angular", "https://angular.dev/tutorials", "Proyecto guiado"),
        ("REST API", "Diseño de APIs REST", "OpenAPI", "https://learn.openapis.org/", "Lectura"),
        ("Git", "Git y colaboración", "Git", "https://git-scm.com/book/es/v2", "Curso"),
        ("Docker", "Introducción a contenedores", "Docker", "https://docs.docker.com/get-started/", "Laboratorio"),
        *((name, *entry) for name, entry in LEARNING_MATERIALS.items()),
    ]
    for competency_name, title, provider, url, resource_type in resources:
        db.add(LearningResource(competency=competencies[competency_name], title=title, provider=provider, url=url, resource_type=resource_type))
    if not PRODUCTION:
        db.add(User(email="postulante@skillgrow.pe", full_name="Usuario de demostración", password_hash=hash_password("Demo123!"), consent=True))
        db.add(User(email="admin@skillgrow.pe", full_name="Administrador SkillGrow", password_hash=hash_password("Admin123!"), consent=True, role="admin"))
    db.commit()


register_catalog(app)
Base.metadata.create_all(engine)
CatalogBase.metadata.create_all(engine)
with SessionLocal() as session:
    seed_data(session)
    repair_seed_links(session, LearningResource)

static_site = os.getenv("STATIC_DIR")
if static_site:
    app.mount("/", StaticFiles(directory=static_site, html=True), name="frontend")

