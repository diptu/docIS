import { useState } from "react";
import { Link } from "react-router";

import { useDeleteDocuments, useDocuments } from "../api/hooks";
import type { DocumentDeleteResult, DocumentListItem } from "../api/types";
import { Badge, STATUS_TONE } from "../components/Badge";
import { StatsCards } from "../components/StatsCards";
import { jobStep, ProgressBar } from "../components/JobProgress";
import { LoadError } from "../components/LoadError";
import { UploadButton } from "../components/UploadButton";
import { FILE_TYPE_LABELS, formatDateTime, formatMoney, humanize } from "../lib/format";

const PAGE_SIZE = 25;

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function deleteSummary(result: DocumentDeleteResult): string {
  const parts = [`${plural(result.deleted.length, "document")} deleted.`];
  if (result.in_progress.length)
    parts.push(
      `${plural(result.in_progress.length, "document")} kept because ${
        result.in_progress.length === 1 ? "it is" : "they are"
      } being processed; try again once processing has finished.`,
    );
  if (result.not_found.length)
    parts.push(`${plural(result.not_found.length, "document")} had already been removed.`);
  return parts.join(" ");
}

export function DocumentsPage() {
  const [status, setStatus] = useState("");
  const [review, setReview] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const remove = useDeleteDocuments();
  const documents = useDocuments({
    offset,
    limit: PAGE_SIZE,
    status: status || undefined,
    needs_review: review === "" ? undefined : review === "yes",
  });

  const items = documents.data?.items ?? [];
  // Only rows in view count, so nothing off-screen is ever deleted.
  const selectedIds = items.filter((d) => selected.has(d.id)).map((d) => d.id);
  const allSelected = items.length > 0 && selectedIds.length === items.length;

  const changeView = (apply: () => void) => {
    apply();
    setSelected(new Set());
    remove.reset();
  };
  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(items.map((d) => d.id)));
  const deleteSelected = () => {
    const names = items.filter((d) => selected.has(d.id)).map((d) => d.file_name);
    const what = names.length === 1 ? `"${names[0]}"` : plural(names.length, "document");
    if (!window.confirm(`Delete ${what}? Its file and extracted data are removed permanently.`))
      return;
    remove.mutate(selectedIds, {
      // Keep only what could not be deleted selected, for another try.
      onSuccess: (result) => {
        setSelected(new Set(result.in_progress));
        // Emptied the last page: step back so the table isn't left blank.
        if (result.deleted.length === items.length && offset > 0)
          setOffset(Math.max(0, offset - PAGE_SIZE));
      },
    });
  };

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

      <StatsCards />

      <div className="flex flex-wrap gap-3 text-sm">
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(e) =>
            changeView(() => {
              setStatus(e.target.value);
              setOffset(0);
            })
          }
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
          onChange={(e) =>
            changeView(() => {
              setReview(e.target.value);
              setOffset(0);
            })
          }
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5"
        >
          <option value="">Any review state</option>
          <option value="yes">Needs review</option>
          <option value="no">No open problems</option>
        </select>
        {selectedIds.length > 0 && (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-slate-600">{selectedIds.length} selected</span>
            <button
              type="button"
              onClick={() => setSelected(new Set())}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 hover:bg-slate-50"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={deleteSelected}
              disabled={remove.isPending}
              className="rounded-md bg-red-600 px-3 py-1.5 font-semibold text-white shadow-sm hover:bg-red-500 disabled:opacity-60"
            >
              {remove.isPending ? "Deleting…" : `Delete ${selectedIds.length}`}
            </button>
          </div>
        )}
      </div>

      {remove.isError && (
        <p role="alert" className="text-sm text-red-700">
          Could not delete: {remove.error.message}
        </p>
      )}
      {remove.isSuccess && (
        <p
          role="status"
          className={`text-sm ${remove.data.in_progress.length ? "text-amber-800" : "text-emerald-700"}`}
        >
          {deleteSummary(remove.data)}
        </p>
      )}

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-10 px-4 py-2">
                <input
                  type="checkbox"
                  aria-label="Select all documents on this page"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = selectedIds.length > 0 && !allSelected;
                  }}
                  disabled={items.length === 0}
                  onChange={toggleAll}
                  className="size-4 accent-blue-600"
                />
              </th>
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
                <td colSpan={7} className="px-4 py-6 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {documents.isError && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center">
                  <LoadError
                    what="your documents"
                    error={documents.error}
                    onRetry={() => void documents.refetch()}
                    retrying={documents.isFetching}
                  />
                </td>
              </tr>
            )}
            {documents.data?.items.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-slate-500">
                  No documents yet. Upload PDFs, images or spreadsheets to get started.
                </td>
              </tr>
            )}
            {items.map((doc) => (
              <Row
                key={doc.id}
                doc={doc}
                selected={selected.has(doc.id)}
                onToggle={() => toggle(doc.id)}
              />
            ))}
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
              onClick={() => changeView(() => setOffset(Math.max(0, offset - PAGE_SIZE)))}
              className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              disabled={offset + PAGE_SIZE >= documents.data.total}
              onClick={() => changeView(() => setOffset(offset + PAGE_SIZE))}
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

function Row({
  doc,
  selected,
  onToggle,
}: {
  doc: DocumentListItem;
  selected: boolean;
  onToggle: () => void;
}) {
  const job = doc.processing;
  const active = job && (job.status === "queued" || job.status === "running");
  return (
    <tr className={selected ? "bg-blue-50" : "hover:bg-slate-50"}>
      <td className="px-4 py-2">
        <input
          type="checkbox"
          aria-label={`Select ${doc.file_name}`}
          checked={selected}
          onChange={onToggle}
          className="size-4 accent-blue-600"
        />
      </td>
      <td className="px-4 py-2">
        <Link to={`/documents/${doc.id}`} className="font-medium text-blue-700 hover:underline">
          {doc.file_name}
        </Link>
        <div className="text-xs text-slate-500">{FILE_TYPE_LABELS[doc.file_type] ?? doc.file_type}</div>
      </td>
      <td className="px-4 py-2">
        {active ? (
          <div className="w-36 space-y-1">
            <Badge tone={job.retry_scheduled ? "amber" : "blue"}>
              {jobStep(job)}
              {job.status === "running" && job.percent !== null && ` ${Math.round(job.percent)}%`}…
            </Badge>
            {job.status === "running" && (
              <ProgressBar percent={job.percent} label={`Progress of ${doc.file_name}`} />
            )}
            {job.retry_scheduled && job.error && (
              <p title={job.error} className="truncate text-xs text-amber-800">
                {job.error}
              </p>
            )}
          </div>
        ) : job?.status === "failed" ? (
          <div className="max-w-48 space-y-1">
            <Badge tone="red">{humanize(job.kind)} failed</Badge>
            {job.error && (
              <p title={job.error} className="truncate text-xs text-red-700">
                {job.error}
              </p>
            )}
          </div>
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
