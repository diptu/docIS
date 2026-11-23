import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "./client";
import { RECOVER_MS, recoverFromOutages } from "./recovery";

let client: QueryClient;
let stop: () => void;

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  stop = recoverFromOutages(client);
});

afterEach(() => {
  stop();
  client.clear();
  vi.useRealTimers();
});

/** Watch a query (as a mounted component would) and let it settle. */
async function watch(key: string, fetcher: () => Promise<string>) {
  const observer = new QueryObserver(client, { queryKey: [key], queryFn: fetcher });
  const unsubscribe = observer.subscribe(() => {});
  await vi.advanceTimersByTimeAsync(0);
  return { observer, unsubscribe };
}

it("reloads a query that failed on an outage once the service is back", async () => {
  const fetcher = vi
    .fn<() => Promise<string>>()
    .mockRejectedValueOnce(new ApiError(503, "Unavailable.", true))
    .mockRejectedValueOnce(new ApiError(503, "Unavailable.", true))
    .mockResolvedValue("documents");
  const { observer, unsubscribe } = await watch("documents", fetcher);
  expect(observer.getCurrentResult().isError).toBe(true);

  await vi.advanceTimersByTimeAsync(RECOVER_MS);
  expect(observer.getCurrentResult().isError).toBe(true);

  await vi.advanceTimersByTimeAsync(RECOVER_MS);
  expect(observer.getCurrentResult().data).toBe("documents");
  expect(fetcher).toHaveBeenCalledTimes(3);

  // Healthy queries are not refetched.
  await vi.advanceTimersByTimeAsync(RECOVER_MS * 3);
  expect(fetcher).toHaveBeenCalledTimes(3);
  unsubscribe();
});

it("leaves errors that retrying cannot fix alone", async () => {
  const fetcher = vi
    .fn<() => Promise<string>>()
    .mockRejectedValue(new ApiError(404, "Not found.", false));
  const { unsubscribe } = await watch("missing", fetcher);

  await vi.advanceTimersByTimeAsync(RECOVER_MS * 3);
  expect(fetcher).toHaveBeenCalledTimes(1);
  unsubscribe();
});

it("stops retrying queries that are no longer on screen", async () => {
  const fetcher = vi
    .fn<() => Promise<string>>()
    .mockRejectedValue(new ApiError(503, "Unavailable.", true));
  const { unsubscribe } = await watch("gone", fetcher);
  unsubscribe();

  await vi.advanceTimersByTimeAsync(RECOVER_MS * 3);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("waits while the browser is offline", async () => {
  const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  const fetcher = vi
    .fn<() => Promise<string>>()
    .mockRejectedValue(new ApiError(0, "Can't connect.", true));
  const { unsubscribe } = await watch("offline", fetcher);

  await vi.advanceTimersByTimeAsync(RECOVER_MS * 3);
  expect(fetcher).toHaveBeenCalledTimes(1);

  online.mockReturnValue(true);
  await vi.advanceTimersByTimeAsync(RECOVER_MS);
  expect(fetcher).toHaveBeenCalledTimes(2);
  unsubscribe();
});
