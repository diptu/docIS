import type {
  DocumentList,
  DocumentListItem,
  DocumentPage,
  DocumentRead,
  InvoiceCorrection,
  InvoiceData,
  InvoiceRead,
  ProcessingJob,
  SheetRows,
  SheetSummary,
  ValidationErrorItem,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** An HTTP error with the backend's `detail` message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg: "..."}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        [d.loc?.slice(1).join("."), d.msg].filter(Boolean).join(": "),
      )
      .join("; ");
  }
  return `Request failed (HTTP ${status})`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new ApiError(response.status, detailMessage(body, response.status));
  }
  return (await response.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface ListParams {
  offset?: number;
  limit?: number;
  status?: string;
  needs_review?: boolean;
}

export const api = {
  listDocuments(params: ListParams = {}): Promise<DocumentList> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    const suffix = query.size ? `?${query}` : "";
    return request(`/documents${suffix}`);
  },
  uploadDocuments(files: File[]): Promise<DocumentListItem[]> {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request("/documents", { method: "POST", body: form });
  },
  getDocument: (id: string): Promise<DocumentRead> => request(`/documents/${id}`),
  getProcessing: (id: string): Promise<ProcessingJob> =>
    request(`/documents/${id}/processing`),
  retryProcessing: (id: string): Promise<ProcessingJob> =>
    request(`/documents/${id}/processing/retry`, { method: "POST" }),
  getInvoice: (id: string): Promise<InvoiceRead> => request(`/documents/${id}/invoice`),
  correctInvoice: (id: string, invoice: InvoiceData): Promise<InvoiceRead> =>
    request(`/documents/${id}/invoice`, json("PUT", invoice)),
  getCorrections: (id: string): Promise<InvoiceCorrection[]> =>
    request(`/documents/${id}/invoice/corrections`),
  setErrorResolved: (
    id: string,
    errorId: string,
    resolved: boolean,
  ): Promise<ValidationErrorItem> =>
    request(`/documents/${id}/validation-errors/${errorId}`, json("PATCH", { resolved })),
  getPages: (id: string): Promise<DocumentPage[]> => request(`/documents/${id}/pages`),
  getSheets: (id: string): Promise<SheetSummary[]> => request(`/documents/${id}/sheets`),
  getSheet: (id: string, sheet: number, offset = 0, limit = 500): Promise<SheetRows> =>
    request(`/documents/${id}/sheets/${sheet}?offset=${offset}&limit=${limit}`),
  fileUrl: (id: string, download = false): string =>
    `${API_BASE}/documents/${id}/file${download ? "?download=true" : ""}`,
};
