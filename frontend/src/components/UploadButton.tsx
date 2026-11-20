import { useRef, useState } from "react";

import { useUpload, useUploadLimits } from "../api/hooks";
import { formatLimit, uploadProblem } from "../lib/uploadLimits";
import { ACCEPT, SupportedFormats } from "./SupportedFormats";

export function UploadButton() {
  const input = useRef<HTMLInputElement>(null);
  const upload = useUpload();
  const limits = useUploadLimits().data;
  const [problem, setProblem] = useState<string | null>(null);

  const send = (files: File[]) => {
    // Without known limits the backend still enforces them.
    const found = limits ? uploadProblem(files, limits) : null;
    setProblem(found);
    if (found) upload.reset();
    else upload.mutate(files);
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        aria-label="Choose files to upload"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length) send(files);
          event.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => input.current?.click()}
        disabled={upload.isPending}
        className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 disabled:opacity-60"
      >
        {upload.isPending ? "Uploading…" : "Upload documents"}
      </button>
      <SupportedFormats />
      {limits && (
        <p className="text-xs text-slate-500">
          Up to {formatLimit(limits.max_file_size_mb)} per file, {limits.max_files_per_upload} files
          per upload
        </p>
      )}
      {(problem !== null || upload.isError) && (
        <p role="alert" className="max-w-md text-right text-sm text-red-700">
          {problem ?? upload.error?.message}
        </p>
      )}
      {upload.isSuccess && !problem && (
        <p className="text-sm text-emerald-700">
          {upload.data.length} file{upload.data.length === 1 ? "" : "s"} uploaded.
        </p>
      )}
    </div>
  );
}
