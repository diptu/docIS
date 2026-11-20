/** File formats the backend accepts (app/services/file_types.py). */
export const SUPPORTED_FORMATS = [
  { label: "PDF", extensions: [".pdf"], description: "PDF documents, text or scanned" },
  { label: "JPG", extensions: [".jpg", ".jpeg"], description: "JPEG images" },
  { label: "PNG", extensions: [".png"], description: "PNG images" },
  { label: "TIFF", extensions: [".tif", ".tiff"], description: "TIFF images, including multi-page scans" },
  { label: "Excel", extensions: [".xlsx", ".xls"], description: "Excel workbooks" },
  { label: "CSV", extensions: [".csv"], description: "Comma-separated values" },
] as const;

/** Value for the file input's `accept` attribute. */
export const ACCEPT = SUPPORTED_FORMATS.flatMap((f) => f.extensions).join(",");

export function SupportedFormats() {
  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5 text-xs">
      <span id="supported-formats" className="text-slate-500">
        Supported formats:
      </span>
      <ul aria-labelledby="supported-formats" className="flex flex-wrap gap-1.5">
        {SUPPORTED_FORMATS.map((format) => (
          <li
            key={format.label}
            title={`${format.description} (${format.extensions.join(", ")})`}
            className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-medium text-slate-700"
          >
            {format.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
