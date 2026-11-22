import type { ReactNode } from "react";
import { LoadError } from "./LoadError";

import { useDocumentStats } from "../api/hooks";
import { formatDuration } from "../lib/format";

const TONES = {
  blue: "bg-blue-50 text-blue-600 ring-blue-100",
  violet: "bg-violet-50 text-violet-600 ring-violet-100",
  green: "bg-emerald-50 text-emerald-600 ring-emerald-100",
  red: "bg-red-50 text-red-600 ring-red-100",
} as const;

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="size-5"
    >
      {children}
    </svg>
  );
}

const DocumentsIcon = () => (
  <Icon>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5M9 13h6M9 17h4" />
  </Icon>
);
const ClockIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Icon>
);
const CheckIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="m8 12.5 2.5 2.5L16 9.5" />
  </Icon>
);
const AlertIcon = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5M12 16h.01" />
  </Icon>
);

function Card({
  label,
  value,
  hint,
  icon,
  tone,
}: {
  label: string;
  value: string;
  hint: string;
  icon: ReactNode;
  tone: keyof typeof TONES;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
    >
      <span className={`rounded-lg p-2 ring-1 ring-inset ${TONES[tone]}`}>{icon}</span>
      <div className="min-w-0">
        <p className="text-sm text-slate-600">{label}</p>
        <p className="text-2xl font-semibold tabular-nums text-slate-900">{value}</p>
        <p className="truncate text-xs text-slate-500">{hint}</p>
      </div>
    </div>
  );
}

const share = (part: number, total: number) =>
  total ? `${Math.round((part / total) * 100)}% of all documents` : "No documents yet";

/** Key figures shown above the documents table. */
export function StatsCards() {
  const stats = useDocumentStats();

  if (stats.isError)
    return (
      <p className="text-sm">
        <LoadError
          what="the statistics"
          error={stats.error}
          onRetry={() => void stats.refetch()}
          retrying={stats.isFetching}
          brief
        />
      </p>
    );

  const s = stats.data;
  const count = (n: number | undefined) => (n === undefined ? "…" : n.toLocaleString());
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Card
        label="Total documents"
        value={count(s?.total)}
        hint={s ? (s.in_progress ? `${s.in_progress} in progress` : "None in progress") : ""}
        icon={<DocumentsIcon />}
        tone="blue"
      />
      <Card
        label="Avg processing time"
        value={s ? formatDuration(s.avg_processing_seconds) : "…"}
        hint="Per completed document"
        icon={<ClockIcon />}
        tone="violet"
      />
      <Card
        label="Completed"
        value={count(s?.completed)}
        hint={s ? share(s.completed, s.total) : ""}
        icon={<CheckIcon />}
        tone="green"
      />
      <Card
        label="Failed"
        value={count(s?.failed)}
        hint={s ? (s.failed ? share(s.failed, s.total) : "Nothing failed") : ""}
        icon={<AlertIcon />}
        tone="red"
      />
    </div>
  );
}
