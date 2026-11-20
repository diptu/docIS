import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";

import { job } from "../test/fixtures";
import { JobHistory, jobSeconds } from "./JobHistory";

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <JobHistory documentId="d1" />
    </QueryClientProvider>,
  );
}

const respond = (body: unknown) =>
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
  );

afterEach(() => vi.restoreAllMocks());

it("lists every job newest first, with attempts, duration and errors", async () => {
  const fetch = respond([
    job({ id: "b", kind: "extract_invoice" }),
    job({ id: "a", status: "failed", attempts: 3, error: "FileNotFoundError: gone", percent: null }),
  ]);
  renderHistory();

  const items = within(await screen.findByRole("list", { name: "Processing jobs" })).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Extract invoice");
  expect(items[0]).toHaveTextContent("Succeeded");
  expect(items[0]).toHaveTextContent("4.0 s");
  expect(items[1]).toHaveTextContent("retried later");
  expect(items[1]).toHaveTextContent("3 of 3");
  expect(within(items[1]!).getByText("FileNotFoundError: gone")).toBeInTheDocument();
  expect(String(fetch.mock.calls[0]![0])).toBe("/api/documents/d1/processing/history");
});

it("says so when there are no jobs", async () => {
  respond([]);
  renderHistory();

  expect(await screen.findByText("This document has no processing jobs.")).toBeInTheDocument();
});

it("measures duration from start to finish, or not at all before start", () => {
  expect(jobSeconds({ started_at: "2026-01-01T00:00:00Z", finished_at: "2026-01-01T00:02:05Z" })).toBe(125);
  expect(jobSeconds({ started_at: null, finished_at: null })).toBeNull();
});
