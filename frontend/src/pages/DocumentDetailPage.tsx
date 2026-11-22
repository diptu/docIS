import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { api } from "../api/client";
import {
  useCorrectInvoice,
  useDeleteDocuments,
  useDocument,
  useFileExists,
  useInvoice,
  useProcessing,
  useRetry,
} from "../api/hooks";
import { Badge, STATUS_TONE } from "../components/Badge";
import { CorrectionHistory } from "../components/CorrectionHistory";
import { DocumentViewer } from "../components/DocumentViewer";
import { InvoiceForm } from "../components/InvoiceForm";
import { JobHistory } from "../components/JobHistory";
import { PagesText } from "../components/PagesText";
import { ProcessingPanel } from "../components/ProcessingPanel";
import { ReviewPanel } from "../components/ReviewPanel";
import { FILE_TYPE_LABELS, formatBytes, formatDateTime, humanize } from "../lib/format";

type Tab = "invoice" | "review" | "text" | "processing" | "history";

export function DocumentDetailPage() {
  const { id = "" } = useParams();
  const document = useDocument(id);
  const fileExists = useFileExists(id);
  const processing = useProcessing(id);
  const invoice = useInvoice(id);
  const correct = useCorrectInvoice(id);
  const retry = useRetry();
  const remove = useDeleteDocuments();
  const navigate = useNavigate();
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("invoice");

  if (document.isPending) return <p className="text-slate-500">Loading…</p>;
  if (document.isError)
    return (
      <p role="alert" className="text-red-700">
        {document.error.message} <Link to="/" className="underline">Back to documents</Link>
      </p>
    );

  const doc = document.data;
  const deleteDocument = () => {
    if (!window.confirm(`Delete "${doc.file_name}"? Its file and extracted data are removed permanently.`))
      return;
    setDeleteError(null);
    remove.mutate([doc.id], {
      onSuccess: (result) => {
        if (result.in_progress.length)
          setDeleteError("This document is being processed; delete it once processing has finished.");
        else void navigate("/");
      },
      onError: (error) => setDeleteError(error.message),
    });
  };
  const job = processing.data;
  const openErrors = invoice.data?.review.errors.filter((e) => !e.resolved).length ?? 0;
  const tabs: [Tab, string][] = [
    ["invoice", "Invoice"],
    ["review", openErrors ? `Review (${openErrors})` : "Review"],
    ["text", "Text"],
    ["processing", job?.status === "failed" ? "Processing (failed)" : "Processing"],
    ["history", "History"],
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/" className="text-sm text-blue-700 hover:underline">
            ← Documents
          </Link>
          <h1 className="mt-1 text-xl font-semibold">{doc.file_name}</h1>
          <p className="text-sm text-slate-600">
            {FILE_TYPE_LABELS[doc.file_type] ?? doc.file_type} · {formatBytes(doc.size_bytes)} ·
            uploaded {formatDateTime(doc.created_at)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={STATUS_TONE[doc.status]}>{humanize(doc.status)}</Badge>
          {fileExists.data === false ? (
            <span
              title="The original file is no longer in storage."
              className="cursor-not-allowed rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-400"
            >
              No file found
            </span>
          ) : (
            <a
              href={api.fileUrl(doc.id, true)}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
            >
              Download original
            </a>
          )}
          <button
            type="button"
            onClick={deleteDocument}
            disabled={remove.isPending}
            className="rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-60"
          >
            {remove.isPending ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>

      {deleteError && (
        <p role="alert" className="text-sm text-red-700">
          Could not delete: {deleteError}
        </p>
      )}

      {job && (
        <ProcessingPanel
          job={job}
          onRetry={() => retry.mutate(id)}
          retrying={retry.isPending}
          retryError={retry.isError ? retry.error.message : null}
        />
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-[75vh] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <DocumentViewer document={doc} />
        </div>

        <div className="flex h-[75vh] flex-col rounded-lg border border-slate-200 bg-white shadow-sm">
          <div role="tablist" className="flex gap-1 border-b border-slate-200 px-3">
            {tabs.map(([key, label]) => (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className={`px-3 py-2.5 text-sm ${
                  tab === key
                    ? "border-b-2 border-blue-600 font-medium text-blue-700"
                    : "text-slate-600 hover:text-slate-900"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            {tab === "text" && <PagesText documentId={id} />}
            {tab === "processing" && <JobHistory documentId={id} />}
            {tab === "history" && <CorrectionHistory documentId={id} />}
            {(tab === "invoice" || tab === "review") &&
              (invoice.isPending ? (
                <p className="text-sm text-slate-500">Loading…</p>
              ) : !invoice.data ? (
                <p className="text-sm text-slate-500">
                  No invoice extracted yet
                  {job && (job.status === "queued" || job.status === "running")
                    ? " — processing is under way."
                    : "."}
                </p>
              ) : tab === "invoice" ? (
                <>
                  {invoice.data.corrected_at && (
                    <p className="mb-3 text-xs text-slate-500">
                      Last corrected {formatDateTime(invoice.data.corrected_at)}
                    </p>
                  )}
                  <InvoiceForm
                    invoice={invoice.data.invoice}
                    checks={invoice.data.validation.checks}
                    saving={correct.isPending}
                    saveError={correct.isError ? correct.error.message : null}
                    onSave={(data) => correct.mutate(data)}
                  />
                </>
              ) : (
                <ReviewPanel documentId={id} invoice={invoice.data} />
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}
