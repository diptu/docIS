import type { ProcessingJob } from "../api/types";
import { formatDateTime, humanize } from "../lib/format";
import { isActive, jobStep, ProgressBar } from "./JobProgress";

/** Live state of a document's latest job: progress, retries and errors. */
export function ProcessingPanel({
  job,
  onRetry,
  retrying,
  retryError,
}: {
  job: ProcessingJob;
  onRetry: () => void;
  retrying: boolean;
  retryError: string | null;
}) {
  if (isActive(job)) {
    const counted = job.status === "running" && job.progress_total;
    return (
      <section
        aria-label="Processing status"
        className={`space-y-2 rounded-lg border p-3 text-sm ${
          job.retry_scheduled
            ? "border-amber-200 bg-amber-50 text-amber-900"
            : "border-blue-200 bg-blue-50 text-blue-900"
        }`}
      >
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{humanize(job.kind)}:</span>
          <span>{jobStep(job)}</span>
          {counted ? (
            <span className="tabular-nums">
              ({job.progress_current}/{job.progress_total})
            </span>
          ) : null}
          <span className="ml-auto text-xs opacity-80">
            Attempt {Math.max(job.attempts, 1)} of {job.max_attempts}
          </span>
        </div>
        {job.status === "running" && (
          <ProgressBar percent={job.percent} label={`${humanize(job.kind)} progress`} />
        )}
        {job.retry_scheduled && job.error && (
          <p className="break-words text-xs">
            Last attempt failed: <span className="font-mono">{job.error}</span>
            {job.next_run_at && <> — next try {formatDateTime(job.next_run_at)}</>}
          </p>
        )}
      </section>
    );
  }
  if (job.status === "failed") {
    return (
      <section
        role="alert"
        aria-label="Processing status"
        className="space-y-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900"
      >
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-medium">
            {humanize(job.kind)} failed after {job.attempts} of {job.max_attempts} attempts
          </span>
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="ml-auto rounded-md bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-500 disabled:opacity-50"
          >
            {retrying ? "Retrying…" : "Retry"}
          </button>
        </div>
        {job.error && <p className="break-words font-mono text-xs">{job.error}</p>}
        {retryError && <p className="text-xs">Could not retry: {retryError}</p>}
      </section>
    );
  }
  return null;
}
