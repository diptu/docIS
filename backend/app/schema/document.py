"""
Document request and response schemas.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentBase(BaseModel):
    """Fields shared by document schemas."""

    file_name: str = Field(min_length=1, max_length=255)
    file_type: str = Field(min_length=1, max_length=100)
    storage_path: str = Field(min_length=1, max_length=1024)
    status: str = Field(min_length=1, max_length=50)


class DocumentCreate(DocumentBase):
    """Payload for creating a document."""


class DocumentUpdate(BaseModel):
    """Payload for partially updating a document."""

    file_name: str | None = Field(default=None, min_length=1, max_length=255)
    file_type: str | None = Field(default=None, min_length=1, max_length=100)
    storage_path: str | None = Field(default=None, min_length=1, max_length=1024)
    status: str | None = Field(default=None, min_length=1, max_length=50)


class DocumentRead(DocumentBase):
    """Document returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
