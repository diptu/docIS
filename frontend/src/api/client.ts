import type {
  DocumentDeleteResult,
  DocumentList,
  DocumentStats,
  DocumentListItem,
  DocumentPage,
  DocumentRead,
  InvoiceCorrection,
  InvoiceData,
  InvoiceRead,
  ProcessingJob,
  ProcessingJobList,
  SearchMode,
  SearchResults,
  SheetRows,
  SheetSummary,
  UploadLimits,
  ValidationErrorItem,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** Status used when the request never got an HTTP response. */
export const NETWORK_ERROR = 0;

/** An HTTP error with the backend's `detail` message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Likely to succeed if retried: server unreachable, restarting or overloaded. */
    readonly transient = false,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Retry queries a few times while the server is restarting; other errors once. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  const transient = error instanceof ApiError && error.transient;
  return failureCount < (transient ? 5 : 1);
}

// Messages for people, not developers: what happened and what to do.
const UNREACHABLE = "Can't connect to the server. Check your connection and try again.";
const UNAVAILABLE = "The service is temporarily unavailable. Please try again in a moment.";
const SOMETHING_WRONG = "Something went wrong. Please try again.";

function statusMessage(status: number): string {
  // 502/503/504, or a 500 without our JSON body (the dev proxy answers that
  // way while the backend restarts): a temporary outage.
  if (status >= 500) return UNAVAILABLE;
  return SOMETHING_WRONG;
}

function detailMessage(body: unknown): string | null {
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
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(NETWORK_ERROR, UNREACHABLE, true);
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = detailMessage(body);
    const { status } = response;
    // A 500 without our JSON body is not an application error (those always
    // carry a detail): the server is down or restarting behind a proxy.
    const transient = [502, 503, 504].includes(status) || (status >= 500 && detail === null);
    throw new ApiError(status, detail ?? statusMessage(status), transient);
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

export interface JobListParams {
  offset?: number;
  limit?: number;
  status?: string;
  kind?: string;
  /** Only each document's most recent job (hide runs that were retried). */
  latest_only?: boolean;
}

function queryString(params: object): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  return query.size ? `?${query}` : "";
}

export const api = {
  listDocuments: (params: ListParams = {}): Promise<DocumentList> =>
    request(`/documents${queryString(params)}`),
  listJobs: (params: JobListParams = {}): Promise<ProcessingJobList> =>
    request(`/processing/jobs${queryString(params)}`),
  getProcessingHistory: (id: string): Promise<ProcessingJob[]> =>
    request(`/documents/${id}/processing/history`),
  search: (q: string, mode: SearchMode, limit = 20): Promise<SearchResults> =>
    request(`/search?${new URLSearchParams({ q, mode, limit: String(limit) })}`),
  getStats: (): Promise<DocumentStats> => request("/documents/stats"),
  getUploadLimits: (): Promise<UploadLimits> => request("/documents/upload-limits"),
  uploadDocuments(files: File[]): Promise<DocumentListItem[]> {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request("/documents", { method: "POST", body: form });
  },
  /** Delete documents and everything extracted from them. Cannot be undone. */
  deleteDocuments: (ids: string[]): Promise<DocumentDeleteResult> =>
    request("/documents/delete", json("POST", { ids })),
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
  /** Whether the original file is still stored (HEAD: no download). */
  async fileExists(id: string): Promise<boolean> {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/documents/${id}/file`, { method: "HEAD" });
    } catch {
      throw new ApiError(NETWORK_ERROR, UNREACHABLE, true);
    }
    if (response.status === 404) return false;
    if (!response.ok) {
      // HEAD responses have no body, so no detail message.
      const transient = response.status >= 500;
      throw new ApiError(response.status, statusMessage(response.status), transient);
    }
    return true;
  },
};
