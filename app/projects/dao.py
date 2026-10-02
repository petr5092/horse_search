from app.dao.base import BaseDAO
from app.projects.models import Projects


class ProjectDAO(BaseDAO):
    model = Projects
