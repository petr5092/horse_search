import re
from typing import Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UploadRequest(BaseModel):
    original_name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    content_type: str = Field(min_length=3, max_length=255)
    sha256: Optional[str] = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @field_validator("original_name")
    @classmethod
    def safe_name(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or "/" in value or "\\" in value or not value.strip():
            raise ValueError("Use a filename without paths or control characters")
        return value

    @field_validator("content_type")
    @classmethod
    def mime_type(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", value):
            raise ValueError("Use a MIME type without parameters")
        return value.lower()


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: int
    original_name: str
    size: int
    content_type: str
    status: str
    sha256: Optional[str]
    created_at: datetime


class UploadResponse(BaseModel):
    file: FileResponse
    url: str
    fields: dict[str, str]
    expires_in: int


class DownloadResponse(BaseModel):
    url: str
    expires_in: int
