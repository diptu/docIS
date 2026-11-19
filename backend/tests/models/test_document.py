import uuid

from sqlalchemy import String

from app.models import Document


def test_document_table_definition() -> None:
    """Verify the documents table has the expected columns and constraints."""
    table = Document.__table__
    columns = table.columns

    assert table.name == "documents"
    assert set(columns.keys()) == {
        "id",
        "file_name",
        "file_type",
        "storage_path",
        "size_bytes",
        "sha256",
        "status",
        "created_at",
        "updated_at",
    }
    assert columns["id"].primary_key is True
    for name in ("file_name", "file_type", "storage_path", "status"):
        assert isinstance(columns[name].type, String)
        assert columns[name].nullable is False


def test_document_id_defaults_to_uuid4() -> None:
    """Verify the primary key default generates a UUID."""
    default = Document.__table__.columns["id"].default

    assert default is not None
    assert isinstance(default.arg(None), uuid.UUID)  # type: ignore[union-attr]
