import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Document, DocumentStatus
from app.processing.processor import DocumentNotProcessableError, DocumentProcessor
from app.repositories.document import DocumentRepository
from app.storage.local import LocalStorage
from tests.helpers import (
    CSV_BYTES,
    concat_pdfs,
    encode,
    make_scanned_text_pdf,
    make_text_image,
    make_text_pdf,
    make_workbook,
    requires_tesseract,
)


async def add_document(
    factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    content: bytes,
    file_type: str = "application/pdf",
) -> uuid.UUID:
    document_id = uuid.uuid4()
    key = f"originals/{document_id}.bin"
    path = storage.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    async with factory() as session:
        await DocumentRepository(session).add_all(
            [
                Document(
                    id=document_id,
                    file_name="doc",
                    file_type=file_type,
                    storage_path=key,
                    status=DocumentStatus.UPLOADED,
                )
            ]
        )
    return document_id


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def load_sheets(
    factory: async_sessionmaker[AsyncSession], document_id: uuid.UUID
) -> list[tuple[int, str, list[list[object]]]]:
    async with factory() as session:
        sheets = await DocumentRepository(session).list_sheets(document_id)
    return [(s.sheet_number, s.name, s.rows) for s in sheets]


async def load(
    factory: async_sessionmaker[AsyncSession], document_id: uuid.UUID
) -> tuple[Document | None, list[tuple[int, str, bool]]]:
    async with factory() as session:
        repository = DocumentRepository(session)
        document = await repository.get(document_id)
        pages = await repository.list_pages(document_id)
    return document, [(p.page_number, p.text, p.needs_ocr) for p in pages]


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def processor(
    session_factory: async_sessionmaker[AsyncSession], storage: LocalStorage
) -> DocumentProcessor:
    return DocumentProcessor(session_factory, storage)


@requires_tesseract
@pytest.mark.asyncio
async def test_process_extracts_pages_and_marks_processed(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    pdf = concat_pdfs(
        make_text_pdf("Hello world from page one"),
        make_scanned_text_pdf(["Scanned second page"]),
    )
    document_id = await add_document(session_factory, storage, pdf)

    await processor.process(document_id)

    document, pages = await load(session_factory, document_id)
    assert document is not None
    assert document.status == DocumentStatus.PROCESSED
    assert pages == [
        (1, "Hello world from page one", False),
        (2, "Scanned second page", False),
    ]


@requires_tesseract
@pytest.mark.asyncio
async def test_process_ocrs_images(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    png = encode(make_text_image(["Photographed receipt"]), "PNG")
    document_id = await add_document(session_factory, storage, png, "image/png")

    await processor.process(document_id)

    document, pages = await load(session_factory, document_id)
    assert document is not None
    assert document.status == DocumentStatus.PROCESSED
    assert pages == [(1, "Photographed receipt", False)]


@pytest.mark.asyncio
async def test_reprocessing_replaces_pages(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    pdf = make_text_pdf("Only page with enough text")
    document_id = await add_document(session_factory, storage, pdf)

    await processor.process(document_id)
    await processor.process(document_id)

    _, pages = await load(session_factory, document_id)
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_unreadable_file_raises_and_saves_nothing(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    document_id = await add_document(session_factory, storage, b"%PDF-garbage")

    with pytest.raises(Exception):  # noqa: B017  (any parser error)
        await processor.process(document_id)

    document, pages = await load(session_factory, document_id)
    assert document is not None
    # Left as processing: the job runner decides between retry and failure.
    assert document.status == DocumentStatus.PROCESSING
    assert pages == []


@pytest.mark.asyncio
async def test_missing_file_raises(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    document_id = await add_document(session_factory, storage, make_text_pdf("x"))
    async with session_factory() as session:
        document = await DocumentRepository(session).get(document_id)
        assert document is not None
        storage.path_for(document.storage_path).unlink()

    with pytest.raises(FileNotFoundError):
        await processor.process(document_id)

    document, _ = await load(session_factory, document_id)
    assert document is not None
    # Left as processing: the job runner decides between retry and failure.
    assert document.status == DocumentStatus.PROCESSING


@pytest.mark.asyncio
async def test_unsupported_and_unknown_documents_are_rejected(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    zip_id = await add_document(session_factory, storage, b"PK", "application/zip")

    with pytest.raises(DocumentNotProcessableError, match="No extractor"):
        await processor.process(zip_id)
    with pytest.raises(DocumentNotProcessableError, match="not found"):
        await processor.process(uuid.uuid4())

    document, pages = await load(session_factory, zip_id)
    assert document is not None
    assert document.status == DocumentStatus.UPLOADED
    assert pages == []


@pytest.mark.asyncio
async def test_process_parses_workbook_sheets(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    workbook = make_workbook({"Q1": [["a", 1]], "Q2": [["b", 2.5]]})
    document_id = await add_document(session_factory, storage, workbook, XLSX)

    await processor.process(document_id)
    await processor.process(document_id)  # reprocessing replaces sheets

    document, pages = await load(session_factory, document_id)
    assert document is not None
    assert document.status == DocumentStatus.PROCESSED
    assert pages == []
    assert await load_sheets(session_factory, document_id) == [
        (1, "Q1", [["a", 1]]),
        (2, "Q2", [["b", 2.5]]),
    ]


@pytest.mark.asyncio
async def test_process_parses_csv_into_named_sheet(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalStorage,
    processor: DocumentProcessor,
) -> None:
    document_id = await add_document(session_factory, storage, CSV_BYTES, "text/csv")

    await processor.process(document_id)

    # add_document names the file "doc", so the sheet is named "doc".
    assert await load_sheets(session_factory, document_id) == [
        (1, "doc", [["name", "amount"], ["alice", "10"], ["bob", "20"]])
    ]
