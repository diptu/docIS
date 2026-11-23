import type { Query, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

/** How often queries that failed on a temporary outage are tried again. */
export const RECOVER_MS = 5000;

/** Failed because the server was unreachable, restarting or overloaded. */
export function failedTemporarily(query: Query): boolean {
  const { error } = query.state;
  return error instanceof ApiError && error.transient;
}

/** On screen, failed on an outage and not already being fetched. */
const recoverable = (query: Query) =>
  failedTemporarily(query) && query.getObserversCount() > 0 && query.state.fetchStatus === "idle";

/**
 * Keep re-fetching queries that gave up during an outage (the database or
 * the API restarting), so the page recovers by itself once the service is
 * back instead of waiting for someone to press "Try again". Queries that
 * failed for another reason (not found, bad request) are left alone.
 * Returns a function that stops the loop.
 */
export function recoverFromOutages(client: QueryClient, intervalMs = RECOVER_MS): () => void {
  const timer = setInterval(() => {
    if (typeof navigator !== "undefined" && navigator.onLine === false) return;
    if (client.getQueryCache().findAll({ predicate: recoverable }).length === 0) return;
    void client.refetchQueries({ predicate: recoverable });
  }, intervalMs);
  return () => clearInterval(timer);
}
