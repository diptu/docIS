import { useCorrections } from "../api/hooks";
import { formatDateTime } from "../lib/format";

const show = (value: unknown): string =>
  value === null || value === undefined
    ? "—"
    : typeof value === "object"
      ? `${(value as unknown[]).length ?? ""} item(s)`
      : String(value);

export function CorrectionHistory({ documentId }: { documentId: string }) {
  const corrections = useCorrections(documentId);
  if (corrections.isPending) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!corrections.data?.length)
    return <p className="text-sm text-slate-500">No manual corrections yet.</p>;
  return (
    <ol className="space-y-3">
      {corrections.data.map((correction) => (
        <li key={correction.id} className="rounded-lg border border-slate-200 p-3 text-sm">
          <div className="mb-2 text-xs text-slate-500">{formatDateTime(correction.created_at)}</div>
          <table className="w-full text-left">
            <tbody>
              {Object.entries(correction.changes).map(([field, change]) => (
                <tr key={field} className="align-top">
                  <td className="py-0.5 pr-3 font-medium">{field}</td>
                  <td className="py-0.5 pr-3 text-red-700 line-through">{show(change.old)}</td>
                  <td className="py-0.5 text-emerald-700">{show(change.new)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </li>
      ))}
    </ol>
  );
}
