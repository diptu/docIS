import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useComparison } from "../api/hooks";
import type {
  CompareCell,
  CompareCounts,
  CompareField,
  CompareLineGroup,
  CompareLineRef,
  CompareStatus,
  ComparedDocument,
} from "../api/types";
import { Badge, type Tone } from "../components/Badge";
import { LoadError } from "../components/LoadError";
import { FILE_TYPE_LABELS } from "../lib/format";

export const MAX_COMPARE = 10;

/** Status shown as icon + word + colour, so it never relies on colour alone. */
const STATUS: Record<CompareStatus, { label: string; tone: Tone; icon: string }> = {
  match: { label: "Match", tone: "green", icon: "✓" },
  mismatch: { label: "Mismatch", tone: "red", icon: "≠" },
  missing: { label: "Missing", tone: "amber", icon: "!" },
  unavailable: { label: "Unavailable", tone: "gray", icon: "–" },
};
const STATUS_ORDER: CompareStatus[] = ["mismatch", "missing", "match", "unavailable"];
const DIFFERENCES = new Set<CompareStatus>(["mismatch", "missing"]);
const NUMERIC = new Set(["money", "quantity", "rate"]);
const GROUPS: [CompareField["group"], string][] = [
  ["header", "Invoice"],
  ["vendor", "Vendor"],
  ["customer", "Customer"],
  ["totals", "Amounts"],
];
const MATCHED_BY: Record<string, string> = {
  reference: "reference line",
  product_code: "matched by product code",
  description: "matched by description",
};

export function StatusBadge({ status }: { status: CompareStatus }) {
  const { label, tone, icon } = STATUS[status];
  return (
    <Badge tone={tone}>
      <span aria-hidden="true">{icon}</span>
      {label}
    </Badge>
  );
}

/** The comparison URL for documents in order (the first is the reference). */
export const compareUrl = (ids: string[]) =>
  `/compare?${new URLSearchParams(ids.map((id) => ["ids", id]))}`;

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const ids = params.getAll("ids");
  const [differencesOnly, setDifferencesOnly] = useState(false);
  const comparison = useComparison(ids);

  const setIds = (next: string[]) =>
    setParams(new URLSearchParams(next.map((id) => ["ids", id])));

  if (ids.length < 2)
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-semibold">Compare documents</h1>
        <p className="text-sm text-slate-600">
          Select two to {MAX_COMPARE} processed documents on the{" "}
          <Link to="/" className="font-medium text-blue-700 hover:underline">
            Documents
          </Link>{" "}
          page and click <span className="font-medium">Compare</span>. Any mix of PDF, image,
          Excel and CSV files works.
        </p>
      </div>
    );

  const data = comparison.data;
  const visible = (status: CompareStatus) => !differencesOnly || DIFFERENCES.has(status);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Compare documents</h1>
          <p className="text-sm text-slate-600">
            Extracted invoice fields side by side. Differences are measured from the
            reference document, or from the first document that has the value.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={differencesOnly}
            onChange={(e) => setDifferencesOnly(e.target.checked)}
            className="size-4 accent-blue-600"
          />
          Show differences only
        </label>
      </div>

      {comparison.isPending && <p className="text-sm text-slate-500">Comparing…</p>}
      {comparison.isError && (
        <p className="text-sm">
          <LoadError
            what="the comparison"
            error={comparison.error}
            onRetry={() => void comparison.refetch()}
            retrying={comparison.isFetching}
          />
        </p>
      )}

      {data && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <Summary title="Invoice fields" counts={data.field_counts} />
            <Summary title="Line items" counts={data.line_counts} />
          </div>

          {data.notes.length > 0 && (
            <ul className="space-y-1 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-900">
              {data.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}

          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <caption className="sr-only">Comparison of {data.documents.length} documents</caption>
              <DocumentHeader
                documents={data.documents}
                onReference={(id) => setIds([id, ...ids.filter((other) => other !== id)])}
                onRemove={
                  ids.length > 2 ? (id) => setIds(ids.filter((other) => other !== id)) : undefined
                }
              />
              <tbody className="divide-y divide-slate-100">
                {GROUPS.map(([group, label]) => {
                  const rows = data.fields.filter((f) => f.group === group && visible(f.status));
                  if (!rows.length) return null;
                  return [
                    <SectionRow key={group} label={label} span={data.documents.length + 2} />,
                    ...rows.map((row) => (
                      <FieldRow key={row.field} row={row} />
                    )),
                  ];
                })}
                {data.fields.length === 0 && (
                  <tr>
                    <td colSpan={data.documents.length + 2} className="px-4 py-6 text-center text-slate-500">
                      None of these documents has extracted invoice fields to compare.
                    </td>
                  </tr>
                )}
                {data.line_items.some((g) => visible(g.status)) && (
                  <SectionRow label="Line items" span={data.documents.length + 2} />
                )}
                {data.line_items
                  .filter((g) => visible(g.status))
                  .map((group) => (
                    <LineGroupRows
                      key={group.position}
                      group={group}
                      documents={data.documents}
                      differencesOnly={differencesOnly}
                    />
                  ))}
              </tbody>
            </table>
          </div>
          {differencesOnly &&
            data.field_counts.mismatch + data.field_counts.missing === 0 &&
            data.line_counts.mismatch + data.line_counts.missing === 0 && (
              <p role="status" className="text-sm text-emerald-700">
                No differences: every value these documents share matches.
              </p>
            )}
        </>
      )}
    </div>
  );
}

function Summary({ title, counts }: { title: string; counts: CompareCounts }) {
  const total = STATUS_ORDER.reduce((sum, s) => sum + counts[s], 0);
  return (
    <section
      aria-label={title}
      className="rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm"
    >
      <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title} <span className="font-normal normal-case">({total})</span>
      </h2>
      <div className="mt-2 flex flex-wrap gap-2">
        {total === 0 ? (
          <span className="text-sm text-slate-500">Nothing to compare</span>
        ) : (
          STATUS_ORDER.filter((s) => counts[s] > 0).map((s) => (
            <Badge key={s} tone={STATUS[s].tone}>
              <span aria-hidden="true">{STATUS[s].icon}</span>
              {counts[s]} {STATUS[s].label.toLowerCase()}
            </Badge>
          ))
        )}
      </div>
    </section>
  );
}

function DocumentHeader({
  documents,
  onReference,
  onRemove,
}: {
  documents: ComparedDocument[];
  onReference: (id: string) => void;
  onRemove?: (id: string) => void;
}) {
  return (
    <thead className="bg-slate-50 text-left align-top">
      <tr>
        <th className="sticky left-0 z-10 bg-slate-50 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Field
        </th>
        <th className="px-4 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Status
        </th>
        {documents.map((doc) => (
          <th key={doc.id} scope="col" className="min-w-44 px-4 py-2 font-normal">
            <Link
              to={`/documents/${doc.id}`}
              className="block max-w-56 truncate font-medium text-blue-700 hover:underline"
              title={doc.file_name}
            >
              {doc.file_name}
            </Link>
            <div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs text-slate-500">
              {FILE_TYPE_LABELS[doc.file_type] ?? doc.file_type}
              {doc.reference && <Badge tone="blue">Reference</Badge>}
              {!doc.invoice_available && <Badge>No invoice</Badge>}
            </div>
            {(!doc.reference || onRemove) && (
              <div className="mt-1 flex gap-2 text-xs">
                {!doc.reference && (
                  <button
                    type="button"
                    onClick={() => onReference(doc.id)}
                    className="text-blue-700 hover:underline"
                    aria-label={`Use ${doc.file_name} as reference`}
                  >
                    Use as reference
                  </button>
                )}
                {onRemove && (
                  <button
                    type="button"
                    onClick={() => onRemove(doc.id)}
                    className="text-slate-600 hover:underline"
                    aria-label={`Remove ${doc.file_name} from comparison`}
                  >
                    Remove
                  </button>
                )}
              </div>
            )}
          </th>
        ))}
      </tr>
    </thead>
  );
}

function SectionRow({ label, span }: { label: string; span: number }) {
  return (
    <tr className="bg-slate-50/60">
      <th
        colSpan={span}
        scope="colgroup"
        className="px-4 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
      >
        {label}
      </th>
    </tr>
  );
}

function FieldRow({ row, indent = false }: { row: CompareField; indent?: boolean }) {
  return (
    <tr>
      <th
        scope="row"
        className={`sticky left-0 z-10 bg-white py-2 pr-4 text-left font-medium text-slate-700 ${indent ? "pl-8" : "pl-4"}`}
      >
        {row.label}
      </th>
      <td className="px-4 py-2">
        <StatusBadge status={row.status} />
      </td>
      {row.cells.map((cell) => (
        <ValueCell
          key={cell.document_id}
          cell={cell}
          row={row}
          baseline={cell.document_id === row.baseline_document_id}
        />
      ))}
    </tr>
  );
}

function ValueCell({
  cell,
  row,
  baseline,
}: {
  cell: CompareCell;
  row: CompareField;
  baseline: boolean;
}) {
  if (cell.presence === "unavailable")
    return <td className="px-4 py-2 text-slate-400">Not available</td>;
  if (cell.presence === "missing")
    return <td className="px-4 py-2 italic text-amber-800">Missing</td>;

  const differs = cell.matches_baseline === false;
  const numeric = NUMERIC.has(row.kind);
  const value = String(cell.value);
  // Show what was compared when normalisation changed more than case/spacing.
  const comparedAs =
    cell.normalized !== null && cell.normalized !== value && !numeric
      ? `Compared as “${cell.normalized}”`
      : undefined;
  return (
    <td className={`px-4 py-2 align-top ${differs ? "bg-red-50" : ""}`} title={comparedAs}>
      <span className={`${numeric ? "tabular-nums" : ""} ${differs ? "font-medium text-red-800" : ""}`}>
        {value}
      </span>
      {differs && <span className="sr-only"> (differs)</span>}
      {numeric && row.status !== "match" && cell.difference !== null && !baseline && (
        <div className={`text-xs tabular-nums ${differs ? "text-red-700" : "text-slate-500"}`}>
          Δ {cell.difference}
        </div>
      )}
      {baseline && row.status === "mismatch" && (
        <div className="text-xs text-slate-500">baseline</div>
      )}
    </td>
  );
}

function lineLabel(group: CompareLineGroup): string {
  const description = group.fields
    .find((f) => f.field === "description")
    ?.cells.find((c) => c.presence === "present")?.value;
  return description ? String(description) : `Line ${group.position}`;
}

function LineRefCell({ line, available }: { line: CompareLineRef; available: boolean }) {
  if (!available) return <td className="px-4 py-2 text-xs text-slate-400">Not available</td>;
  if (line.line_number === null)
    return <td className="px-4 py-2 text-xs italic text-amber-800">Not on this invoice</td>;
  return (
    <td className="px-4 py-2 text-xs text-slate-600">
      Line {line.line_number}
      {line.matched_by && <span className="text-slate-500"> · {MATCHED_BY[line.matched_by]}</span>}
    </td>
  );
}

function LineGroupRows({
  group,
  documents,
  differencesOnly,
}: {
  group: CompareLineGroup;
  documents: ComparedDocument[];
  differencesOnly: boolean;
}) {
  const fields = group.fields.filter((f) => !differencesOnly || DIFFERENCES.has(f.status));
  return (
    <>
      <tr className="border-t-2 border-slate-200">
        <th
          scope="rowgroup"
          className="sticky left-0 z-10 max-w-64 truncate bg-white px-4 py-2 text-left font-semibold text-slate-800"
          title={lineLabel(group)}
        >
          {group.position}. {lineLabel(group)}
        </th>
        <td className="px-4 py-2">
          <StatusBadge status={group.status} />
        </td>
        {group.lines.map((line, i) => (
          <LineRefCell key={line.document_id} line={line} available={documents[i]?.invoice_available ?? false} />
        ))}
      </tr>
      {fields.map((row) => (
        <FieldRow key={row.field} row={row} indent />
      ))}
    </>
  );
}
