import { useState } from "react";
import { Link } from "react-router";

import { useJobs, useRetry } from "../api/hooks";
import type { JobStatus, ProcessingJobListItem } from "../api/types";
import { jobSeconds } from "../components/JobHistory";
import { jobStep, JobStatusBadge, ProgressBar } from "../components/JobProgress";
import { formatDateTime, formatDuration, humanize } from "../lib/format";

const PAGE_SIZE = 25;
const FILTERS: [JobStatus | "", string][] = [
  ["", "All"],
  ["queued", "Queued"],
  ["running", "Running"],
  ["failed", "Failed"],
  ["succeeded", "Succeeded"],
];

export function ProcessingPage() {
  const [status, setStatus] = useState<JobStatus | "">("");
  const [kind, setKind] = useState("");
  const [latestOnly, setLatestOnly] = useState(true);
  const [offset, setOffset] = useState(0);
  const jobs = useJobs({
    offset,
    limit: PAGE_SIZE,
    status,
    kind,
    latest_only: latestOnly,
  });
  const retry = useRetry();

  const counts = jobs.data?.counts ?? {};
  const all = Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0);
  const total = jobs.data?.total ?? 0;
  const reset = (change: () => void) => {
    change();
    setOffset(0);
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Processing</h1>
        <p className="text-sm text-slate-600">
          Background jobs across all documents: progress, retries and errors. Updates live
          while jobs are running.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <div role="group" aria-label="Filter by job status" className="flex flex-wrap gap-1">
          {FILTERS.map(([value, label]) => (
            <button
              key={label}
              type="button"
              aria-pressed={status === value}
              onClick={() => reset(() => setStatus(value))}
              className={`rounded-md border px-3 py-1.5 ${
                status === value
                  ? "border-blue-600 bg-blue-600 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              {label}{" "}
              <span className="tabular-nums opacity-80">
                {jobs.data ? (value ? (counts[value] ?? 0) : all) : "…"}
              </span>
            </button>
          ))}
        </div>
        <select
          aria-label="Filter by job kind"
          value={kind}
          onChange={(e) => reset(() => setKind(e.target.value))}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5"
        >
          <option value="">All job kinds</option>
          <option value="extract_content">Extract content</option>
          <option value="extract_invoice">Extract invoice</option>
        </select>
        <label className="flex items-center gap-2 text-slate-700">
          <input
            type="checkbox"
            checked={latestOnly}
            onChange={(e) => reset(() => setLatestOnly(e.target.checked))}
            className="size-4 accent-blue-600"
          />
          Hide retried runs
        </label>
      </div>

      {retry.isError && (
        <p role="alert" className="text-sm text-red-700">
          Could not retry: {retry.error.message}
        </p>
      )}

      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Document</th>
              <th className="px-4 py-2">Job</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2">Progress</th>
              <th className="px-4 py-2 text-right">Attempts</th>
              <th className="px-4 py-2">Started</th>
              <th className="px-4 py-2 text-right">Duration</th>
              <th className="px-4 py-2">Error</th>
              <th className="px-4 py-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {jobs.isPending ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            ) : jobs.isError ? (
              <tr>
                <td colSpan={9} role="alert" className="px-4 py-6 text-center text-red-700">
                  {jobs.error.message}
                </td>
              </tr>
            ) : jobs.data.items.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-slate-500">
                  No jobs match these filters.
                </td>
              </tr>
            ) : (
              jobs.data.items.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  retrying={retry.isPending && retry.variables === job.document_id}
                  onRetry={() => retry.mutate(job.document_id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 disabled:opacity-50"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function JobRow({
  job,
  retrying,
  onRetry,
}: {
  job: ProcessingJobListItem;
  retrying: boolean;
  onRetry: () => void;
}) {
  return (
    <tr className="align-top hover:bg-slate-50">
      <td className="px-4 py-2">
        <Link to={`/documents/${job.document_id}`} className="font-medium text-blue-700 hover:underline">
          {job.file_name}
        </Link>
        {!job.is_latest && <div className="text-xs text-slate-500">Earlier run</div>}
      </td>
      <td className="px-4 py-2 whitespace-nowrap">{humanize(job.kind)}</td>
      <td className="px-4 py-2">
        <JobStatusBadge job={job} />
      </td>
      <td className="w-48 px-4 py-2">
        <div className="text-xs text-slate-600">
          {jobStep(job)}
          {job.status === "running" && job.progress_total ? (
            <span className="tabular-nums">
              {" "}
              ({job.progress_current}/{job.progress_total})
            </span>
          ) : null}
        </div>
        {job.status === "running" && (
          <div className="mt-1">
            <ProgressBar percent={job.percent} label={`Progress of ${job.file_name}`} />
          </div>
        )}
      </td>
      <td className="px-4 py-2 text-right tabular-nums">
        {job.attempts}/{job.max_attempts}
      </td>
      <td className="px-4 py-2 whitespace-nowrap text-slate-600">{formatDateTime(job.started_at)}</td>
      <td className="px-4 py-2 text-right tabular-nums">{formatDuration(jobSeconds(job))}</td>
      <td className="max-w-xs px-4 py-2">
        {job.error ? (
          <p
            title={job.error}
            className={`line-clamp-2 break-words font-mono text-xs ${
              job.status === "failed" ? "text-red-700" : "text-amber-800"
            }`}
          >
            {job.error}
          </p>
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className="px-4 py-2 text-right">
        {job.status === "failed" && job.is_latest && (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            aria-label={`Retry ${job.file_name}`}
            className="rounded-md border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            {retrying ? "Retrying…" : "Retry"}
          </button>
        )}
      </td>
    </tr>
  );
}
