import { usePages } from "../api/hooks";
import { Badge } from "./Badge";

export function PagesText({ documentId }: { documentId: string }) {
  const pages = usePages(documentId);
  if (pages.isPending) return <p className="text-sm text-slate-500">Loading text…</p>;
  if (!pages.data?.length)
    return <p className="text-sm text-slate-500">No page text (spreadsheets are shown as sheets).</p>;
  return (
    <div className="space-y-4">
      {pages.data.map((page) => (
        <section key={page.page_number} className="rounded-lg border border-slate-200">
          <header className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 text-xs text-slate-600">
            <span className="font-semibold">Page {page.page_number}</span>
            <Badge tone={page.extraction_method === "ocr" ? "blue" : "gray"}>
              {page.extraction_method === "ocr" ? "OCR" : "Text layer"}
            </Badge>
            {page.ocr_confidence !== null && (
              <Badge tone={page.ocr_confidence < 80 ? "amber" : "green"}>
                {page.ocr_confidence.toFixed(0)}% confidence
              </Badge>
            )}
          </header>
          <pre className="whitespace-pre-wrap p-3 font-mono text-xs leading-relaxed">
            {page.text || <span className="text-slate-400">(no text)</span>}
          </pre>
        </section>
      ))}
    </div>
  );
}
