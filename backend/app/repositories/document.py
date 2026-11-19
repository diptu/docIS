"""
Persistence operations for documents.
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document


class DocumentRepository:
    """Data access for the documents table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_all(self, documents: Sequence[Document]) -> list[Document]:
        """Insert documents in one transaction and return them refreshed."""
        self.session.add_all(documents)
        await self.session.commit()
        for document in documents:
            await self.session.refresh(document)
        return list(documents)
