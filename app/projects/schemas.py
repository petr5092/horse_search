from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class SProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Название проекта датасета")
    schema_version: str = Field("1.0", max_length=50, description="Версия схемы разметки")
    target_size: int = Field(640, description="Фиксированный размер стороны кадра (640x640 px)")
    description: Optional[str] = Field(None, description="Описание условий съемки и назначения")


class SProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None


class SProjectOut(BaseModel):
    id: int
    name: str
    schema_version: str
    target_size: int
    description: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True
