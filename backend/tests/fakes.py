"""Test doubles shared across test modules."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.extraction.llm import LLMExtraction
    from app.extraction.schema import InvoiceData


class FakeSession:
    """Minimal stand-in for AsyncSession that records added objects."""

    def __init__(self, fail_on_commit: bool = False) -> None:
        self.added: list[Any] = []
        self.committed = False
        self.fail_on_commit = fail_on_commit

    def add_all(self, objects: Any) -> None:
        self.added.extend(objects)

    async def commit(self) -> None:
        if self.fail_on_commit:
            raise RuntimeError("database unavailable")
        self.committed = True

    async def refresh(self, obj: Any) -> None:
        now = datetime.now(UTC)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        obj.created_at = now
        obj.updated_at = now


# ------------------------------------------------------------- AI extraction


def sample_invoice(**overrides: Any) -> "InvoiceData":
    """A complete, realistic extraction result."""
    import datetime as dt

    from app.extraction.schema import InvoiceData, InvoiceLineItem, Party

    values: dict[str, Any] = {
        "is_invoice": True,
        "invoice_number": "INV-2026-001",
        "invoice_date": dt.date(2026, 11, 3),
        "due_date": dt.date(2026, 12, 3),
        "currency": "EUR",
        "purchase_order_number": "PO-7781",
        "payment_terms": "Net 30",
        "vendor": Party(
            name="ACME GmbH",
            address="Hauptstr. 1, 10115 Berlin, Germany",
            tax_id="DE123456789",
            email="billing@acme.example",
            phone=None,
        ),
        "customer": Party(
            name="Globex Ltd", address=None, tax_id=None, email=None, phone=None
        ),
        "line_items": [
            InvoiceLineItem(
                description="Consulting",
                product_code="SVC-1",
                quantity=10,
                unit="h",
                unit_price=125.0,
                tax_rate=19,
                amount=1250.0,
            )
        ],
        "subtotal": 1250.0,
        "tax_amount": 237.5,
        "total_amount": 1487.5,
        "amount_due": None,
        "uncertain_fields": [],
    }
    values.update(overrides)
    return InvoiceData(**values)


class FakeInvoiceLLM:
    """Stands in for Claude: returns a fixed result or raises."""

    def __init__(
        self, data: "InvoiceData | None" = None, error: Exception | None = None
    ) -> None:
        self.data = data or sample_invoice()
        self.error = error
        self.texts: list[str] = []

    async def extract(self, document_text: str) -> "LLMExtraction":
        from app.extraction.llm import LLMExtraction

        self.texts.append(document_text)
        if self.error is not None:
            raise self.error
        return LLMExtraction(
            data=self.data,
            model="claude-opus-5-5",
            input_tokens=1200,
            output_tokens=300,
            cache_read_tokens=800,
        )


# ---------------------------------------------------------------- search


class FakeEmbedder:
    """Deterministic stand-in for the embedding model: hashed bag of words,
    so texts sharing words are similar. 384 dimensions, unit length."""

    dim = 384

    def _embed(self, text: str) -> list[float]:
        import hashlib
        import math
        import re

        vector = [0.0] * self.dim
        for word in re.findall(r"\w+", text.lower()):
            digest = hashlib.md5(word.encode(), usedforsecurity=False).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_passages(self, texts: Any) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
