import { Link } from "react-router";

import { useSetErrorResolved } from "../api/hooks";
import type { InvoiceRead } from "../api/types";
import { formatDateTime, humanize } from "../lib/format";
import { Badge } from "./Badge";

export function ReviewPanel({ documentId, invoice }: { documentId: string; invoice: InvoiceRead }) {
  const resolve = useSetErrorResolved(documentId);
  const { checks } = invoice.validation;
  const errors = invoice.review.errors;

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">Amount checks</h3>
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
          {checks.length === 0 && (
            <li className="p-3 text-sm text-slate-500">Not an invoice: no checks run.</li>
          )}
          {checks.map((check) => (
            <li key={check.code} className="flex items-start gap-3 p-3 text-sm">
              <Badge
                tone={check.status === "passed" ? "green" : check.status === "failed" ? "red" : "gray"}
              >
                {check.status}
              </Badge>
              <div>
                <div className="font-medium">{humanize(check.code)}</div>
                <div className="text-slate-600">{check.message}</div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold text-slate-700">
          Problems to review{" "}
          <span className="font-normal text-slate-500">
            ({errors.filter((e) => !e.resolved).length} open)
          </span>
        </h3>
        {errors.length === 0 ? (
          <p className="rounded-lg border border-slate-200 p-3 text-sm text-slate-500">
            No problems found.
          </p>
        ) : (
          <ul className="space-y-2">
            {errors.map((error) => {
              const ids = (error.details.document_ids as string[] | undefined) ?? [];
              return (
                <li
                  key={error.id}
                  className={`rounded-lg border p-3 text-sm ${
                    error.resolved ? "border-slate-200 bg-slate-50 opacity-70" : "border-slate-200"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Badge tone={error.severity === "error" ? "red" : "amber"}>{error.severity}</Badge>
                    <span className="font-medium">{humanize(error.code)}</span>
                    {error.resolved && <Badge tone="green">resolved</Badge>}
                    <button
                      type="button"
                      disabled={resolve.isPending}
                      onClick={() => resolve.mutate({ errorId: error.id, resolved: !error.resolved })}
                      className="ml-auto rounded-md border border-slate-300 px-2 py-1 text-xs font-medium hover:bg-slate-50 disabled:opacity-50"
                    >
                      {error.resolved ? "Reopen" : "Mark resolved"}
                    </button>
                  </div>
                  <p className="mt-1 text-slate-700">{error.message}</p>
                  {ids.length > 0 && (
                    <p className="mt-1 text-slate-600">
                      See:{" "}
                      {ids.map((id, i) => (
                        <span key={id}>
                          {i > 0 && ", "}
                          <Link to={`/documents/${id}`} className="text-blue-700 underline">
                            document {id.slice(0, 8)}
                          </Link>
                        </span>
                      ))}
                    </p>
                  )}
                  {error.resolved_at && (
                    <p className="mt-1 text-xs text-slate-500">
                      Resolved {formatDateTime(error.resolved_at)}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {invoice.duplicates.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">Duplicates</h3>
          <ul className="space-y-1 text-sm">
            {invoice.duplicates.map((d) => (
              <li key={d.document_id}>
                <Link to={`/documents/${d.document_id}`} className="text-blue-700 underline">
                  {d.file_name}
                </Link>{" "}
                <span className="text-slate-500">({humanize(d.match)})</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
