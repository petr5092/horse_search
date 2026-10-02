from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import engine, Base
from app.users.models import Users
from app.projects.models import Projects
from app.users.router import router as router_users
from app.projects.router import router as router_projects

# Create tables in database via SQLAlchemy ORM metadata
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="API системы создания датасета детекции лошадей (НИИ ВИМ, 2026)",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers by objects
app.include_router(router_users)
app.include_router(router_projects)


@app.get("/", tags=["Система"])
def root():
    return {
        "status": "online",
        "system": settings.PROJECT_NAME,
        "docs": "/docs",
    }
