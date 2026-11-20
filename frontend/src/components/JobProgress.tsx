import type { JobStatus, ProcessingSummary } from "../api/types";
import { humanize } from "../lib/format";
import { Badge, type Tone } from "./Badge";

type JobState = Pick<ProcessingSummary, "status" | "stage" | "retry_scheduled">;

export const isActive = (job: { status: JobStatus }) =>
  job.status === "queued" || job.status === "running";

/** Where a job is right now, in words: "Waiting in queue", "OCR", … */
export function jobStep(job: JobState): string {
  if (job.retry_scheduled) return "Retry scheduled";
  if (job.status === "queued") return "Waiting in queue";
  if (job.status === "running") return job.stage ? stageName(job.stage) : "Starting";
  return humanize(job.status);
}

const STAGE_NAMES: Record<string, string> = {
  extracting_text: "Extracting text",
  ocr: "OCR",
  parsing_sheets: "Parsing sheets",
  parsing_csv: "Parsing CSV",
  extracting_invoice: "AI invoice extraction",
  indexing: "Indexing for search",
  saving: "Saving",
};

export const stageName = (stage: string) => STAGE_NAMES[stage] ?? humanize(stage);

const JOB_TONE: Record<JobStatus, Tone> = {
  queued: "gray",
  running: "blue",
  succeeded: "green",
  failed: "red",
};

export function JobStatusBadge({ job }: { job: JobState }) {
  if (job.retry_scheduled) return <Badge tone="amber">Retrying</Badge>;
  return <Badge tone={JOB_TONE[job.status]}>{humanize(job.status)}</Badge>;
}

/** A progress bar; without a percentage it pulses (progress unknown). */
export function ProgressBar({ percent, label }: { percent: number | null; label: string }) {
  const known = percent !== null;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={known ? percent : undefined}
      className="h-1.5 w-full overflow-hidden rounded bg-slate-200"
    >
      <div
        className={`h-full rounded bg-blue-600 transition-all ${known ? "" : "w-1/3 animate-pulse"}`}
        style={known ? { width: `${percent}%` } : undefined}
      />
    </div>
  );
}
