import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import type { ProcessingJobListItem } from "../api/types";
import { jobItem } from "../test/fixtures";
import { ProcessingPage } from "./ProcessingPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProcessingPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const RUNNING = jobItem({
  id: "j1",
  document_id: "d1",
  file_name: "scan.tiff",
  status: "running",
  stage: "ocr",
  progress_current: 2,
  progress_total: 5,
  percent: 40,
  finished_at: null,
});
const FAILED = jobItem({
  id: "j2",
  document_id: "d2",
  file_name: "broken.pdf",
  status: "failed",
  attempts: 3,
  error: "FileNotFoundError: original missing",
  percent: null,
});
const OLD_FAILURE = jobItem({
  id: "j3",
  document_id: "d3",
  file_name: "recovered.pdf",
  status: "failed",
  is_latest: false,
  error: "TimeoutError",
});

function mockJobs(items: ProcessingJobListItem[], counts: Record<string, number>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    if (init?.method === "POST") return json({ ...FAILED, status: "queued", attempts: 0 }, 202);
    return json({ items, total: items.length, offset: 0, limit: 25, counts });
  });
}

const requested = (fetch: ReturnType<typeof mockJobs>) =>
  fetch.mock.calls.map(([url, init]) => `${init?.method ?? "GET"} ${String(url)}`);

afterEach(() => vi.restoreAllMocks());

it("shows each job's progress, attempts and error with status counts", async () => {
  const fetch = mockJobs([RUNNING, FAILED, OLD_FAILURE], { running: 1, failed: 2, succeeded: 4 });
  renderPage();

  const running = (await screen.findByRole("link", { name: "scan.tiff" })).closest("tr")!;
  expect(within(running).getByText("Running")).toBeInTheDocument();
  expect(within(running).getByText(/OCR/)).toHaveTextContent("OCR (2/5)");
  expect(within(running).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");

  const failed = screen.getByRole("link", { name: "broken.pdf" }).closest("tr")!;
  expect(within(failed).getByText("FileNotFoundError: original missing")).toBeInTheDocument();
  expect(within(failed).getByText("3/3")).toBeInTheDocument();

  const group = screen.getByRole("group", { name: "Filter by job status" });
  expect(within(group).getByRole("button", { name: "All 7" })).toHaveAttribute("aria-pressed", "true");
  expect(within(group).getByRole("button", { name: "Failed 2" })).toBeInTheDocument();
  expect(within(group).getByRole("button", { name: "Queued 0" })).toBeInTheDocument();
  expect(requested(fetch)[0]).toBe("GET /api/processing/jobs?offset=0&limit=25&latest_only=true");
});

it("offers retry only for a document's current failed job", async () => {
  const fetch = mockJobs([FAILED, OLD_FAILURE], { failed: 2 });
  renderPage();

  await userEvent.click(await screen.findByRole("button", { name: "Retry broken.pdf" }));

  expect(screen.queryByRole("button", { name: "Retry recovered.pdf" })).not.toBeInTheDocument();
  expect(screen.getByText("Earlier run")).toBeInTheDocument();
  expect(requested(fetch)).toContain("POST /api/documents/d2/processing/retry");
});

it("filters by status and kind, and can include retried runs", async () => {
  const fetch = mockJobs([], {});
  renderPage();
  await screen.findByText("No jobs match these filters.");

  await userEvent.click(screen.getByRole("button", { name: /^Failed/ }));
  await userEvent.selectOptions(screen.getByLabelText("Filter by job kind"), "extract_invoice");
  await userEvent.click(screen.getByLabelText("Hide retried runs"));

  expect(requested(fetch)).toEqual(
    expect.arrayContaining([
      "GET /api/processing/jobs?offset=0&limit=25&status=failed&latest_only=true",
      "GET /api/processing/jobs?offset=0&limit=25&status=failed&kind=extract_invoice&latest_only=true",
      "GET /api/processing/jobs?offset=0&limit=25&status=failed&kind=extract_invoice&latest_only=false",
    ]),
  );
});

it("reports a failed request", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "Database unavailable" }, 503));
  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable");
});
