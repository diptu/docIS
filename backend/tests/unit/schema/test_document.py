import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models import Document
from app.schema.document import DocumentCreate, DocumentRead, DocumentUpdate

PAYLOAD = {
    "file_name": "report.pdf",
    "file_type": "application/pdf",
    "storage_path": "documents/report.pdf",
    "status": "uploaded",
}


def test_document_create_accepts_valid_payload() -> None:
    """Verify a complete payload validates."""
    document = DocumentCreate(**PAYLOAD)

    assert document.model_dump() == PAYLOAD


@pytest.mark.parametrize("field", list(PAYLOAD))
def test_document_create_rejects_empty_fields(field: str) -> None:
    """Verify required string fields cannot be empty."""
    with pytest.raises(ValidationError):
        DocumentCreate(**{**PAYLOAD, field: ""})


def test_document_update_allows_partial_payload() -> None:
    """Verify updates only include the fields that were set."""
    update = DocumentUpdate(status="processed")

    assert update.model_dump(exclude_unset=True) == {"status": "processed"}


def test_document_read_from_orm_object() -> None:
    """Verify DocumentRead serializes a Document ORM instance."""
    now = datetime.now(UTC)
    doc_id = uuid.uuid4()
    orm = Document(id=doc_id, created_at=now, updated_at=now, **PAYLOAD)

    result = DocumentRead.model_validate(orm)

    assert result.id == doc_id
    assert result.created_at == now
    assert result.file_name == "report.pdf"
