"""DocumentRepository write paths on SQLite: transactional delete and the
cleanup of duplicate flags that point at deleted documents."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import DocumentValidationError
from app.repositories.document import DocumentRepository
from tests.support.factories import add_document

Factory = async_sessionmaker[AsyncSession]


async def _flag(factory: Factory, document_id: uuid.UUID, *others: uuid.UUID) -> None:
    async with factory() as session:
        session.add(
            DocumentValidationError(
                document_id=document_id,
                code="duplicate",
                severity="error",
                message="Uploaded again.",
                details={"document_ids": [str(o) for o in others]},
            )
        )
        await session.commit()


async def _flags(
    factory: Factory, document_id: uuid.UUID
) -> list[tuple[list[str], bool]]:
    async with factory() as session:
        errors = await DocumentRepository(session).list_validation_errors(document_id)
    return [(e.details["document_ids"], e.resolved) for e in errors]


async def test_delete_prunes_and_resolves_only_affected_duplicate_flags(
    session_factory: Factory,
) -> None:
    """A flag naming a deleted document loses that ID (resolved when none
    remain); flags about other documents are left exactly as they were."""
    original, copy_a, copy_b, unrelated, other = [
        await add_document(session_factory) for _ in range(5)
    ]
    await _flag(session_factory, original, copy_a, copy_b)
    await _flag(session_factory, unrelated, other)

    async with session_factory() as session:
        await DocumentRepository(session).delete_documents([copy_a])
    assert await _flags(session_factory, original) == [([str(copy_b)], False)]

    async with session_factory() as session:
        await DocumentRepository(session).delete_documents([copy_b])
    assert await _flags(session_factory, original) == [([], True)]
    assert await _flags(session_factory, unrelated) == [([str(other)], False)]


async def test_a_failing_delete_rolls_back_completely(
    session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If cleanup fails mid-transaction, nothing is deleted."""
    document_id = await add_document(session_factory)

    async def fail(self: DocumentRepository, deleted: object) -> None:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(DocumentRepository, "_drop_duplicate_references", fail)

    async with session_factory() as session:
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await DocumentRepository(session).delete_documents([document_id])

    async with session_factory() as session:
        assert await DocumentRepository(session).get(document_id) is not None


async def test_deleting_nothing_known_is_a_no_op(session_factory: Factory) -> None:
    async with session_factory() as session:
        documents, busy = await DocumentRepository(session).delete_documents(
            [uuid.uuid4()]
        )

    assert (documents, busy) == ([], set())
