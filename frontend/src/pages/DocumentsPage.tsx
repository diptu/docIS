import { useState } from "react";
import { Link } from "react-router";

import { useDocuments } from "../api/hooks";
import type { DocumentListItem } from "../api/types";
import { Badge, STATUS_TONE } from "../components/Badge";
import { UploadButton } from "../components/UploadButton";
import { FILE_TYPE_LABELS, formatDateTime, formatMoney, humanize } from "../lib/format";

const PAGE_SIZE = 25;

export function DocumentsPage() {
  const [status, setStatus] = useState("");
  const [review, setReview] = useState("");
  const [offset, setOffset] = useState(0);
  const documents = useDocuments({
    offset,
    limit: PAGE_SIZE,
    status: status || undefined,
    needs_review: review === "" ? undefined : review === "yes",
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Documents</h1>
          <p className="text-sm text-slate-600">
            Uploaded files, their processing state and extracted invoices.
          </p>
        </div>
        <UploadButton />
      </div>

      <div className="flex flex-wrap gap-3 text-sm">
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5"
        >
          <option value="">All statuses</option>
          {["uploaded", "processing", "processed", "failed"].map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by review"
          value={review}
          onChange={(e) => {
            setReview(e.target.value);
            setOffset(0);
          }}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5"
        >
          <option value="">Any review state</option>
          <option value="yes">Needs review</option>
          <option value="no">No open problems</option>
        </select>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">File</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Invoice</th>
              <th className="px-4 py-2 text-right">Total</th>
              <th className="px-4 py-2">Review</th>
              <th className="px-4 py-2">Uploaded</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {documents.isPending && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {documents.isError && (
              <tr>
                <td colSpan={6} role="alert" className="px-4 py-6 text-center text-red-700">
                  Could not load documents: {documents.error.message}
                </td>
              </tr>
            )}
            {documents.data?.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-slate-500">
                  No documents yet. Upload PDFs, images or spreadsheets to get started.
                </td>
              </tr>
            )}
            {documents.data?.items.map((doc) => <Row key={doc.id} doc={doc} />)}
          </tbody>
        </table>
      </div>

      {documents.data && documents.data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, documents.data.total)} of{" "}
            {documents.data.total}
          </span>
          <div className="flex gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              disabled={offset + PAGE_SIZE >= documents.data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ doc }: { doc: DocumentListItem }) {
  const job = doc.processing;
  const active = job && (job.status === "queued" || job.status === "running");
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-2">
        <Link to={`/documents/${doc.id}`} className="font-medium text-blue-700 hover:underline">
          {doc.file_name}
        </Link>
        <div className="text-xs text-slate-500">{FILE_TYPE_LABELS[doc.file_type] ?? doc.file_type}</div>
      </td>
      <td className="px-4 py-2">
        {active ? (
          <Badge tone="blue">{humanize(job.stage ?? job.status)}…</Badge>
        ) : job?.status === "failed" ? (
          <Badge tone="red">{humanize(job.kind)} failed</Badge>
        ) : (
          <Badge tone={STATUS_TONE[doc.status]}>{humanize(doc.status)}</Badge>
        )}
      </td>
      <td className="px-4 py-2">
        {doc.invoice ? (
          doc.invoice.is_invoice ? (
            <>
              <div>{doc.invoice.invoice_number ?? "—"}</div>
              <div className="text-xs text-slate-500">{doc.invoice.vendor_name ?? "Unknown vendor"}</div>
            </>
          ) : (
            <span className="text-slate-500">Not an invoice</span>
          )
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className="px-4 py-2 text-right tabular-nums">
        {doc.invoice ? formatMoney(doc.invoice.total_amount, doc.invoice.currency) : "—"}
      </td>
      <td className="px-4 py-2">
        {doc.unresolved_errors > 0 ? (
          <Badge tone="amber">{doc.unresolved_errors} open</Badge>
        ) : doc.invoice ? (
          <Badge tone="green">OK</Badge>
        ) : null}
        {doc.invoice?.corrected && (
          <span className="ml-1">
            <Badge>edited</Badge>
          </span>
        )}
      </td>
      <td className="px-4 py-2 text-slate-600">{formatDateTime(doc.created_at)}</td>
    </tr>
  );
}
