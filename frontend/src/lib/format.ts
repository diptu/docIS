export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatMoney(
  amount: string | number | null,
  currency: string | null,
): string {
  if (amount === null) return "—";
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (currency && /^[A-Z]{3}$/.test(currency)) {
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(
        value,
      );
    } catch {
      // Unknown currency code: fall through to a plain number.
    }
  }
  return `${value.toLocaleString(undefined, { minimumFractionDigits: 2 })}${currency ? ` ${currency}` : ""}`;
}

/** 0.42 -> "0.4 s", 12.3 -> "12.3 s", 125 -> "2m 05s", 3720 -> "1h 02m" */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  // Round first, so 59.96 reads "1m 00s" rather than "60.0 s".
  if (Math.round(seconds * 10) < 600) return `${seconds.toFixed(1)} s`;
  const total = Math.round(seconds);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (total < 3600) return `${Math.floor(total / 60)}m ${pad(total % 60)}s`;
  return `${Math.floor(total / 3600)}h ${pad(Math.floor((total % 3600) / 60))}m`;
}

/** "extract_invoice" -> "Extract invoice" */
export function humanize(code: string): string {
  const text = code.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export const FILE_TYPE_LABELS: Record<string, string> = {
  "application/pdf": "PDF",
  "image/jpeg": "JPEG",
  "image/png": "PNG",
  "image/tiff": "TIFF",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "application/vnd.ms-excel": "XLS",
  "text/csv": "CSV",
};
