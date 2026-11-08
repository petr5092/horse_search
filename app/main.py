from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import settings
from app.database import engine, Base
from app.users.models import Users
from app.files.models import StoredFile
from app.projects.models import Project, ProjectMember, LabelSchemaVersion
from app.assets.models import Asset, ImageVersion
from app.annotation_gateway.client import AnnotationClient
from app.annotation_gateway.openapi import install_annotation_openapi
from app.annotation_gateway.router import router as router_annotation
from app.files.router import router as router_files
from app.users.router import router as router_users
from app.projects.router import router as router_projects
from app.assets.router import router as router_assets
from app.api_errors import install_api_errors


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        if settings.AUTO_CREATE_TABLES:
            await conn.run_sync(Base.metadata.create_all)
        elif settings.ENFORCE_MIGRATIONS:
            config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
            config.set_main_option('script_location', str(Path(__file__).resolve().parent / 'migrations'))
            expected = set(ScriptDirectory.from_config(config).get_heads())
            try:
                actual = set((await conn.execute(text('SELECT version_num FROM alembic_version'))).scalars())
            except Exception as exc:
                raise RuntimeError('Database migrations are missing; run alembic upgrade head') from exc
            if actual != expected:
                raise RuntimeError('Database migrations are pending; run alembic upgrade head')
    app.state.annotation_client = AnnotationClient.from_settings(settings)
    try:
        yield
    finally:
        await app.state.annotation_client.aclose()
        await engine.dispose()


app = FastAPI(title=settings.PROJECT_NAME,version='0.2.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=settings.API_CORS_ORIGINS,allow_credentials=True,
                   allow_methods=['*'],allow_headers=['*'])
install_api_errors(app)
for router in [router_users,router_files,router_projects,router_assets,router_annotation]:
    app.include_router(router)


@app.get('/',tags=['Система'])
async def root():
    return {'status':'online','system':settings.PROJECT_NAME,'docs':'/docs'}


install_annotation_openapi(app)
