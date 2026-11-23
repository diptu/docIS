import { ApiError } from "../api/client";

interface LoadErrorProps {
  /** What failed to load, e.g. "your documents". */
  what: string;
  error: Error;
  onRetry: () => void;
  retrying: boolean;
  /** Leave out the explanation of a temporary outage (shown elsewhere on the page). */
  brief?: boolean;
}

/** A friendly "couldn't load" message with a Try again button. During a
 * temporary outage the page reloads by itself once the service is back
 * (see ``api/recovery``), and the message says so. */
export function LoadError({ what, error, onRetry, retrying, brief = false }: LoadErrorProps) {
  const temporary = error instanceof ApiError && error.transient;
  return (
    <span role="alert" className="text-red-700">
      <span className="font-medium">We couldn't load {what}.</span>
      {!(brief && temporary) && <> {error.message}</>}
      {temporary && !brief && <> We'll keep trying automatically.</>}{" "}
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        className="font-medium underline hover:no-underline disabled:opacity-50"
      >
        {retrying ? "Retrying…" : "Try again"}
      </button>
    </span>
  );
}
