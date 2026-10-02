from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import init_db
from app.users.router import router as router_users

# Initialize tables via pure raw SQL DDL
init_db()

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


@app.get("/", tags=["Система"])
def root():
    return {
        "status": "online",
        "system": settings.PROJECT_NAME,
        "docs": "/docs",
    }
