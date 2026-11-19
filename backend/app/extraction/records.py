"""
Conversion between the extraction schema (InvoiceData) and database rows.
"""

import uuid
from decimal import Decimal

from app.extraction.llm import LLMExtraction
from app.extraction.normalize import invoice_number_key, vendor_key
from app.extraction.schema import InvoiceData, InvoiceLineItem, Party
from app.models.invoice import Invoice
from app.models.invoice import InvoiceLineItem as LineItemRow


def _clip(value: str | None, length: int) -> str | None:
    return value[:length] if value is not None else None


def _dec(value: float | None) -> Decimal | None:
    # Via str: 0.1 becomes Decimal("0.1"), not 0.1000000000000000055...
    return Decimal(str(value)) if value is not None else None


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def invoice_from_data(
    document_id: uuid.UUID, data: InvoiceData, result: LLMExtraction
) -> Invoice:
    """Rows for a (normalised) extraction: the invoice and its line items.

    Text longer than its column is cut to fit; amounts are stored exactly.
    """
    vendor, customer = data.vendor, data.customer
    return Invoice(
        document_id=document_id,
        is_invoice=data.is_invoice,
        invoice_number=_clip(data.invoice_number, 100),
        invoice_date=data.invoice_date,
        due_date=data.due_date,
        currency=_clip(data.currency, 10),
        purchase_order_number=_clip(data.purchase_order_number, 100),
        payment_terms=_clip(data.payment_terms, 255),
        vendor_name=_clip(vendor.name, 255),
        vendor_address=vendor.address,
        vendor_tax_id=_clip(vendor.tax_id, 100),
        vendor_email=_clip(vendor.email, 255),
        vendor_phone=_clip(vendor.phone, 100),
        customer_name=_clip(customer.name, 255),
        customer_address=customer.address,
        customer_tax_id=_clip(customer.tax_id, 100),
        customer_email=_clip(customer.email, 255),
        customer_phone=_clip(customer.phone, 100),
        subtotal=_dec(data.subtotal),
        tax=_dec(data.tax_amount),
        total_amount=_dec(data.total_amount),
        amount_due=_dec(data.amount_due),
        uncertain_fields=list(data.uncertain_fields),
        invoice_number_key=invoice_number_key(data.invoice_number),
        vendor_key=vendor_key(vendor),
        model=_clip(result.model, 100) or "",
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        line_items=[
            LineItemRow(
                line_number=number,
                description=item.description,
                product_code=_clip(item.product_code, 100),
                quantity=_dec(item.quantity),
                unit=_clip(item.unit, 50),
                unit_price=_dec(item.unit_price),
                tax_rate=_dec(item.tax_rate),
                amount=_dec(item.amount),
            )
            for number, item in enumerate(data.line_items, start=1)
        ],
    )


def data_from_invoice(invoice: Invoice) -> InvoiceData:
    """Rebuild the schema-valid result from the stored rows."""
    return InvoiceData(
        is_invoice=invoice.is_invoice,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        purchase_order_number=invoice.purchase_order_number,
        payment_terms=invoice.payment_terms,
        vendor=Party(
            name=invoice.vendor_name,
            address=invoice.vendor_address,
            tax_id=invoice.vendor_tax_id,
            email=invoice.vendor_email,
            phone=invoice.vendor_phone,
        ),
        customer=Party(
            name=invoice.customer_name,
            address=invoice.customer_address,
            tax_id=invoice.customer_tax_id,
            email=invoice.customer_email,
            phone=invoice.customer_phone,
        ),
        line_items=[
            InvoiceLineItem(
                description=item.description,
                product_code=item.product_code,
                quantity=_float(item.quantity),
                unit=item.unit,
                unit_price=_float(item.unit_price),
                tax_rate=_float(item.tax_rate),
                amount=_float(item.amount),
            )
            for item in invoice.line_items
        ],
        subtotal=_float(invoice.subtotal),
        tax_amount=_float(invoice.tax),
        total_amount=_float(invoice.total_amount),
        amount_due=_float(invoice.amount_due),
        uncertain_fields=invoice.uncertain_fields,
    )
