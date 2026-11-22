import { useProcessingHistory } from "../api/hooks";
import type { ProcessingJob } from "../api/types";
import { formatDateTime, formatDuration, humanize } from "../lib/format";
import { isActive, jobStep, JobStatusBadge, ProgressBar } from "./JobProgress";

/** Seconds between two timestamps, or until now while still running. */
export function jobSeconds(job: Pick<ProcessingJob, "started_at" | "finished_at">): number | null {
  if (!job.started_at) return null;
  const end = job.finished_at ? Date.parse(job.finished_at) : Date.now();
  return Math.max(0, (end - Date.parse(job.started_at)) / 1000);
}

/** Every processing job of a document, newest first, with its errors. */
export function JobHistory({ documentId }: { documentId: string }) {
  const history = useProcessingHistory(documentId);

  if (history.isPending) return <p className="text-sm text-slate-500">Loading…</p>;
  if (history.isError)
    return (
      <p role="alert" className="text-sm text-red-700">
        {history.error.message}
      </p>
    );
  if (!history.data.length)
    return <p className="text-sm text-slate-500">This document has no processing jobs.</p>;

  return (
    <ol aria-label="Processing jobs" className="space-y-3">
      {history.data.map((job, index) => (
        <li key={job.id} className="rounded-md border border-slate-200 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{humanize(job.kind)}</span>
            <JobStatusBadge job={job} />
            {index > 0 && job.status === "failed" && (
              <span className="text-xs text-slate-500">retried later</span>
            )}
            <span className="ml-auto text-xs text-slate-500">
              {formatDateTime(job.created_at)}
            </span>
          </div>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-600 sm:grid-cols-4">
            <div>
              <dt className="text-slate-400">Step</dt>
              <dd>{jobStep(job)}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Attempts</dt>
              <dd className="tabular-nums">
                {job.attempts} of {job.max_attempts}
              </dd>
            </div>
            <div>
              <dt className="text-slate-400">Started</dt>
              <dd>{formatDateTime(job.started_at)}</dd>
            </div>
            <div>
              <dt className="text-slate-400">Duration</dt>
              <dd className="tabular-nums">{formatDuration(jobSeconds(job))}</dd>
            </div>
          </dl>
          {job.status === "running" && (
            <div className="mt-2">
              <ProgressBar percent={job.percent} label={`${humanize(job.kind)} progress`} />
            </div>
          )}
          {job.error && (
            <p
              className={`mt-2 break-words rounded px-2 py-1 text-xs ${
                isActive(job) ? "bg-amber-50 text-amber-900" : "bg-red-50 text-red-800"
              }`}
            >
              {job.error}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
