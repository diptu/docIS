// Types mirroring the backend API responses (app/schema, app/extraction).

export type DocumentStatus = "uploaded" | "processing" | "processed" | "failed";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface DocumentRead {
  id: string;
  file_name: string;
  file_type: string;
  storage_path: string;
  status: DocumentStatus;
  size_bytes: number | null;
  sha256: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProcessingSummary {
  kind: string;
  status: JobStatus;
  stage: string | null;
}

export interface InvoiceSummary {
  is_invoice: boolean;
  invoice_number: string | null;
  vendor_name: string | null;
  invoice_date: string | null;
  total_amount: string | null; // decimal string
  currency: string | null;
  corrected: boolean;
}

export interface DocumentListItem extends DocumentRead {
  processing: ProcessingSummary | null;
  invoice: InvoiceSummary | null;
  unresolved_errors: number;
}

export interface DocumentList {
  items: DocumentListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface ProcessingJob {
  id: string;
  kind: string;
  status: JobStatus;
  stage: string | null;
  progress_current: number;
  progress_total: number | null;
  attempts: number;
  max_attempts: number;
  error: string | null;
  next_run_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  retry_scheduled: boolean;
  percent: number | null;
}

export interface Party {
  name: string | null;
  address: string | null;
  tax_id: string | null;
  email: string | null;
  phone: string | null;
}

export interface InvoiceLineItem {
  description: string | null;
  product_code: string | null;
  quantity: number | null;
  unit: string | null;
  unit_price: number | null;
  tax_rate: number | null;
  amount: number | null;
}

export interface InvoiceData {
  is_invoice: boolean;
  invoice_number: string | null;
  invoice_date: string | null; // YYYY-MM-DD
  due_date: string | null;
  currency: string | null;
  purchase_order_number: string | null;
  payment_terms: string | null;
  vendor: Party;
  customer: Party;
  line_items: InvoiceLineItem[];
  subtotal: number | null;
  tax_amount: number | null;
  total_amount: number | null;
  amount_due: number | null;
  uncertain_fields: string[];
}

export interface ValidationCheck {
  code: string;
  status: "passed" | "failed" | "skipped";
  message: string;
  expected: string | null;
  actual: string | null;
}

export interface ValidationErrorItem {
  id: string;
  code: string;
  severity: "error" | "warning";
  message: string;
  details: Record<string, unknown>;
  resolved: boolean;
  resolved_at: string | null;
  created_at: string;
}

export interface InvoiceRead {
  document_id: string;
  invoice: InvoiceData;
  corrected_at: string | null;
  validation: { status: "passed" | "failed" | "not_checked"; checks: ValidationCheck[] };
  duplicates: { document_id: string; file_name: string; match: string }[];
  review: { needs_review: boolean; errors: ValidationErrorItem[] };
  extraction: {
    model: string;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
  };
  extracted_at: string;
}

export interface DocumentPage {
  page_number: number;
  text: string;
  char_count: number;
  extraction_method: "text_layer" | "ocr";
  needs_ocr: boolean;
  ocr_confidence: number | null;
}

export interface SheetSummary {
  sheet_number: number;
  name: string;
  row_count: number;
  column_count: number;
  is_hidden: boolean;
  truncated: boolean;
}

export interface SheetRows extends SheetSummary {
  offset: number;
  rows: (string | number | boolean | null)[][];
}

export interface InvoiceCorrection {
  id: string;
  changes: Record<string, { old: unknown; new: unknown }>;
  created_at: string;
}
