import { useEffect, useState } from "react";

import { useSheet, useSheets } from "../api/hooks";

export function SheetGrid({ documentId }: { documentId: string }) {
  const sheets = useSheets(documentId);
  const [active, setActive] = useState<number | null>(null);
  useEffect(() => {
    if (active === null && sheets.data?.[0]) setActive(sheets.data[0].sheet_number);
  }, [active, sheets.data]);
  const sheet = useSheet(documentId, active);

  if (sheets.isPending) return <p className="p-4 text-sm text-slate-500">Loading sheets…</p>;
  if (!sheets.data?.length)
    return <p className="p-4 text-sm text-slate-500">No sheets parsed yet.</p>;

  return (
    <div className="flex h-full flex-col">
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-slate-200 px-2">
        {sheets.data.map((s) => (
          <button
            key={s.sheet_number}
            role="tab"
            aria-selected={s.sheet_number === active}
            onClick={() => setActive(s.sheet_number)}
            className={`whitespace-nowrap px-3 py-2 text-sm ${
              s.sheet_number === active
                ? "border-b-2 border-blue-600 font-medium text-blue-700"
                : "text-slate-600 hover:text-slate-900"
            }`}
          >
            {s.name}
            {s.is_hidden && <span className="ml-1 text-xs text-slate-400">(hidden)</span>}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-auto">
        {sheet.data && (
          <>
            <table className="min-w-full border-collapse text-xs">
              <tbody>
                {sheet.data.rows.map((row, r) => (
                  <tr key={r} className="odd:bg-white even:bg-slate-50">
                    <th className="sticky left-0 border border-slate-200 bg-slate-100 px-2 py-1 text-right font-normal text-slate-400">
                      {sheet.data.offset + r + 1}
                    </th>
                    {row.map((cell, c) => (
                      <td key={c} className="whitespace-pre border border-slate-200 px-2 py-1">
                        {cell === null ? "" : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {sheet.data.row_count > sheet.data.rows.length && (
              <p className="p-2 text-xs text-slate-500">
                Showing the first {sheet.data.rows.length} of {sheet.data.row_count} rows.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
