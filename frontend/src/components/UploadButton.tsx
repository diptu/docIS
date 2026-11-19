import { useRef } from "react";

import { useUpload } from "../api/hooks";

const ACCEPT = ".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv";

export function UploadButton() {
  const input = useRef<HTMLInputElement>(null);
  const upload = useUpload();

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
          if (files.length) upload.mutate(files);
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
      {upload.isError && (
        <p role="alert" className="max-w-md text-right text-sm text-red-700">
          {upload.error.message}
        </p>
      )}
      {upload.isSuccess && (
        <p className="text-sm text-emerald-700">
          {upload.data.length} file{upload.data.length === 1 ? "" : "s"} uploaded.
        </p>
      )}
    </div>
  );
}
