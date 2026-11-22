import { api } from "../api/client";
import { useFileExists } from "../api/hooks";
import type { DocumentRead } from "../api/types";
import { SheetGrid } from "./SheetGrid";

const SPREADSHEETS = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "text/csv",
]);

/** Shown when the stored original is gone (deleted from storage). */
export function NoFileFound({ fileName }: { fileName: string }) {
  return (
    <div
      role="status"
      className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-sm text-slate-600"
    >
      <p className="text-base font-medium text-slate-800">No file found</p>
      <p>
        The original file <span className="font-medium">{fileName}</span> is no longer in storage.
      </p>
      <p>Any text and invoice data already extracted from it are still available.</p>
    </div>
  );
}

/** Shows the original: PDFs in the browser's viewer, images inline,
 *  spreadsheets as their parsed sheets. */
export function DocumentViewer({ document }: { document: DocumentRead }) {
  const url = api.fileUrl(document.id);
  const fileExists = useFileExists(document.id);

  // Spreadsheets show their parsed rows from the database, not the file.
  if (SPREADSHEETS.has(document.file_type)) {
    return <SheetGrid documentId={document.id} />;
  }
  if (fileExists.isPending) {
    return <p className="p-8 text-center text-sm text-slate-500">Loading…</p>;
  }
  // If the check itself failed (e.g. server restarting), still try to show it.
  if (fileExists.data === false) return <NoFileFound fileName={document.file_name} />;
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
