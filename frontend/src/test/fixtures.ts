import type {
  DocumentListItem,
  InvoiceData,
  ProcessingJob,
  ProcessingJobListItem,
  ProcessingSummary,
} from "../api/types";

export const invoice = (overrides: Partial<InvoiceData> = {}): InvoiceData => ({
  is_invoice: true,
  invoice_number: "INV-2026-001",
  invoice_date: "2026-11-03",
  due_date: null,
  currency: "EUR",
  purchase_order_number: null,
  payment_terms: "Net 30",
  vendor: { name: "ACME GmbH", address: null, tax_id: "DE123", email: null, phone: null },
  customer: { name: "Globex Ltd", address: null, tax_id: null, email: null, phone: null },
  line_items: [
    {
      description: "Consulting",
      product_code: "SVC-1",
      quantity: 10,
      unit: "h",
      unit_price: 125,
      tax_rate: 19,
      amount: 1250,
    },
  ],
  subtotal: 1250,
  tax_amount: 237.5,
  total_amount: 1500,
  amount_due: null,
  uncertain_fields: ["invoice_date"],
  ...overrides,
});

export const summary = (overrides: Partial<ProcessingSummary> = {}): ProcessingSummary => ({
  kind: "extract_invoice",
  status: "succeeded",
  stage: "saving",
  percent: 100,
  attempts: 1,
  max_attempts: 3,
  retry_scheduled: false,
  error: null,
  ...overrides,
});

export const job = (overrides: Partial<ProcessingJob> = {}): ProcessingJob => ({
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  kind: "extract_content",
  status: "succeeded",
  stage: "saving",
  progress_current: 1,
  progress_total: 1,
  attempts: 1,
  max_attempts: 3,
  error: null,
  next_run_at: null,
  created_at: "2026-11-19T10:00:00Z",
  started_at: "2026-11-19T10:00:01Z",
  finished_at: "2026-11-19T10:00:05Z",
  retry_scheduled: false,
  percent: 100,
  ...overrides,
});

export const jobItem = (
  overrides: Partial<ProcessingJobListItem> = {},
): ProcessingJobListItem => ({
  ...job(),
  document_id: "11111111-1111-1111-1111-111111111111",
  file_name: "invoice.pdf",
  is_latest: true,
  ...overrides,
});

export const listItem = (overrides: Partial<DocumentListItem> = {}): DocumentListItem => ({
  id: "11111111-1111-1111-1111-111111111111",
  file_name: "invoice.pdf",
  file_type: "application/pdf",
  storage_path: "originals/11/x.pdf",
  status: "processed",
  size_bytes: 2048,
  sha256: "abc",
  created_at: "2026-11-19T10:00:00Z",
  updated_at: "2026-11-19T10:00:00Z",
  processing: summary(),
  invoice: {
    is_invoice: true,
    invoice_number: "INV-2026-001",
    vendor_name: "ACME GmbH",
    invoice_date: "2026-11-03",
    total_amount: "1487.5000",
    currency: "EUR",
    corrected: false,
  },
  unresolved_errors: 0,
  ...overrides,
});
