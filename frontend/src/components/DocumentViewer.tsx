import { api } from "../api/client";
import type { DocumentRead } from "../api/types";
import { SheetGrid } from "./SheetGrid";

const SPREADSHEETS = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/csv",
]);

/** Shows the original: PDFs in the browser's viewer, images inline,
 *  spreadsheets as their parsed sheets. */
export function DocumentViewer({ document }: { document: DocumentRead }) {
  const url = api.fileUrl(document.id);

  if (document.file_type === "application/pdf") {
    return <iframe title={document.file_name} src={url} className="h-full w-full" />;
  }
  if (document.file_type === "image/jpeg" || document.file_type === "image/png") {
    return (
      <div className="flex h-full items-start justify-center overflow-auto bg-slate-100 p-4">
        <img src={url} alt={document.file_name} className="max-w-full shadow" />
      </div>
    );
  }
  if (SPREADSHEETS.has(document.file_type)) {
    return <SheetGrid documentId={document.id} />;
  }
  // TIFF: most browsers cannot display it.
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center text-sm text-slate-600">
      <p>This file type can't be shown in the browser.</p>
      <p>The OCR text is in the "Text" tab.</p>
      <a
        href={api.fileUrl(document.id, true)}
        className="font-medium text-blue-700 underline hover:text-blue-900"
      >
        Download {document.file_name}
      </a>
    </div>
  );
}
