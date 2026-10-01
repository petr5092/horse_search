from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base, SessionLocal
from app.models.user import User, UserRole
from app.models.audit import AuditEvent
from app.core.security import get_password_hash
from app.routers import auth_router, users_router


# Create tables immediately on module load to guarantee schema existence
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize DB tables (idempotent)
    Base.metadata.create_all(bind=engine)

    # 2. Seed initial Administrator if no users exist
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.email == settings.FIRST_SUPERUSER_EMAIL).first()
        if not admin_user:
            admin_user = User(
                email=settings.FIRST_SUPERUSER_EMAIL,
                full_name=settings.FIRST_SUPERUSER_NAME,
                hashed_password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(admin_user)
            db.commit()
            db.refresh(admin_user)

            # Log system seed in AuditEvent
            init_event = AuditEvent(
                actor_id=admin_user.id,
                actor_email=admin_user.email,
                actor_role=admin_user.role.value,
                action="SYSTEM_INITIALIZED",
                entity_type="system",
                entity_id="init_01",
                details=f"Создан базовый администратор: {admin_user.email}",
            )
            db.add(init_event)
            db.commit()
    finally:
        db.close()

    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "API программного средства для создания датасета детекции лошадей на изображениях "
        "фиксированной размерности (640×640 px). Научно-исследовательский институт ВИМ, 2026."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
app.include_router(users_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Система"])
def root():
    return {
        "status": "online",
        "system": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
        "roles_supported": ["admin", "manager", "annotator", "reviewer"],
    }


@app.get("/health", tags=["Система"])
def healthcheck():
    return {"status": "healthy"}
