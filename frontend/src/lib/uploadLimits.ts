import type { UploadLimits } from "../api/types";

/** "20 MB", "0.5 MB" */
export const formatLimit = (mb: number) => `${Number(mb.toFixed(2))} MB`;

/**
 * Why a selection breaks the upload limits, or null if it doesn't. The
 * backend enforces the same limits; checking first saves sending files
 * that would be refused.
 */
export function uploadProblem(files: File[], limits: UploadLimits): string | null {
  if (files.length > limits.max_files_per_upload) {
    return `Too many files: ${files.length} selected, the maximum is ${limits.max_files_per_upload} per upload.`;
  }
  const tooLarge = files.filter((f) => f.size > limits.max_file_size_bytes).map((f) => f.name);
  if (tooLarge.length) {
    const names = tooLarge.length > 3 ? `${tooLarge.slice(0, 3).join(", ")} and ${tooLarge.length - 3} more` : tooLarge.join(", ");
    return `Larger than ${formatLimit(limits.max_file_size_mb)}: ${names}. Nothing was uploaded.`;
  }
  return null;
}
