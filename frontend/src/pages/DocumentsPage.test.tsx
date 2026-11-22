import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";

import { listItem, summary } from "../test/fixtures";
import { DocumentsPage } from "./DocumentsPage";

let location = "";
function LocationSpy() {
  const { pathname, search } = useLocation();
  location = pathname + search;
  return null;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DocumentsPage />
        <LocationSpy />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const STATS = { total: 4, completed: 2, failed: 1, in_progress: 1, avg_processing_seconds: 125 };
const isStats = (url: unknown) => String(url).endsWith("/documents/stats");
const statsResponse = () => new Response(JSON.stringify(STATS), { status: 200 });
const LIMITS = { max_file_size_bytes: 20 * 1024 * 1024, max_file_size_mb: 20, max_files_per_upload: 20 };
/** Stats and upload limits, which every render of the page also fetches. */
const sideResponse = (url: unknown) =>
  isStats(url)
    ? statsResponse()
    : String(url).endsWith("/documents/upload-limits")
      ? new Response(JSON.stringify(LIMITS), { status: 200 })
      : null;

function mockList(items: ReturnType<typeof listItem>[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (url) =>
    sideResponse(url) ?? new Response(JSON.stringify({ items, total: items.length, offset: 0, limit: 25 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => vi.restoreAllMocks());

it("lists documents with their invoice and review state", async () => {
  mockList([
    listItem(),
    listItem({
      id: "22222222-2222-2222-2222-222222222222",
      file_name: "scan.png",
      file_type: "image/png",
      status: "processing",
      processing: summary({ kind: "extract_content", status: "running", stage: "ocr", percent: null }),
      invoice: null,
    }),
    listItem({
      id: "33333333-3333-3333-3333-333333333333",
      file_name: "dup.pdf",
      unresolved_errors: 2,
    }),
  ]);

  renderPage();

  const link = await screen.findByRole("link", { name: "invoice.pdf" });
  expect(link).toHaveAttribute("href", "/documents/11111111-1111-1111-1111-111111111111");
  const [, first, second, third] = screen.getAllByRole("row");
  expect(within(first!).getByText("INV-2026-001")).toBeInTheDocument();
  expect(within(first!).getByText("OK")).toBeInTheDocument();
  expect(within(first!).getByText(/1,487\.50/)).toBeInTheDocument();
  expect(within(second!).getByText("OCR…")).toBeInTheDocument();
  expect(within(third!).getByText("2 open")).toBeInTheDocument();
});

it("filters by review state", async () => {
  const fetch = mockList([]);
  renderPage();
  await screen.findByText(/No documents yet/);

  await userEvent.selectOptions(screen.getByLabelText("Filter by review"), "yes");

  await vi.waitFor(() =>
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/documents?offset=0&limit=25&needs_review=true",
      undefined,
    ),
  );
});

/** The full text of every alert on the page, once they have appeared. */
const alertTexts = () => screen.queryAllByRole("alert").map((alert) => alert.textContent);

it("shows load errors", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ detail: "Your session has expired." }), { status: 401 }),
  );

  renderPage();

  await vi.waitFor(() =>
    expect(alertTexts()).toEqual(
      expect.arrayContaining([
        "We couldn't load the statistics. Your session has expired. Try again",
        "We couldn't load your documents. Your session has expired. Try again",
      ]),
    ),
  );
});

it("explains a temporary outage once, in plain language", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(
      JSON.stringify({
        detail: "The service is temporarily unavailable. Please try again in a moment.",
      }),
      { status: 503 },
    ),
  );

  renderPage();

  // The stats card doesn't repeat the explanation the table gives.
  await vi.waitFor(() =>
    expect(alertTexts()).toEqual(
      expect.arrayContaining([
        "We couldn't load the statistics. Try again",
        "We couldn't load your documents. The service is temporarily unavailable. Please try again in a moment. Try again",
      ]),
    ),
  );
});

it("loads documents again on Try again once the server is back", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ detail: "Unavailable." }), { status: 503 }),
  );
  renderPage();
  await vi.waitFor(() =>
    expect(alertTexts().some((t) => t?.startsWith("We couldn't load your documents"))).toBe(true),
  );
  const row = screen
    .getAllByRole("alert")
    .find((alert) => alert.textContent?.startsWith("We couldn't load your documents"))!;

  fetch.mockImplementation(async (input) =>
    String(input).startsWith("/api/documents?")
      ? new Response(JSON.stringify({ items: [], total: 0, offset: 0, limit: 25 }))
      : new Response(JSON.stringify({ detail: "Unavailable." }), { status: 503 }),
  );
  await userEvent.click(within(row).getByRole("button", { name: "Try again" }));

  expect(await screen.findByText(/No documents yet/)).toBeInTheDocument();
  expect(alertTexts().some((t) => t?.includes("your documents"))).toBe(false);
});

it("shows the KPI cards", async () => {
  mockList([listItem()]);
  renderPage();

  await screen.findByText("1 in progress");
  const card = (name: string) => screen.findByRole("group", { name });
  expect(await card("Total documents")).toHaveTextContent("41 in progress");
  expect(await card("Avg processing time")).toHaveTextContent("2m 05s");
  expect(await card("Completed")).toHaveTextContent("250% of all documents");
  expect(await card("Failed")).toHaveTextContent("125% of all documents");
});

describe("deleting documents", () => {
  const second = () =>
    listItem({ id: "22222222-2222-2222-2222-222222222222", file_name: "scan.png" });

  function mockApi(result: object) {
    let items = [listItem(), second()];
    return vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
      const side = sideResponse(url);
      if (side) return side;
      if (init?.method === "POST") {
        const { ids } = JSON.parse(String(init.body)) as { ids: string[] };
        const kept = (result as { in_progress: string[] }).in_progress;
        items = items.filter((d) => !ids.includes(d.id) || kept.includes(d.id));
        return new Response(JSON.stringify(result), { status: 200 });
      }
      return new Response(
        JSON.stringify({ items, total: items.length, offset: 0, limit: 25 }),
        { status: 200 },
      );
    });
  }

  it("deletes the selected documents after confirmation", async () => {
    const fetch = mockApi({ deleted: [second().id], in_progress: [], not_found: [] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    await userEvent.click(await screen.findByLabelText("Select scan.png"));
    await userEvent.click(screen.getByRole("button", { name: "Delete 1" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('"scan.png"'));
    expect(fetch).toHaveBeenCalledWith(
      "/api/documents/delete",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ ids: [second().id] }) }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("1 document deleted.");
    await vi.waitFor(() => expect(screen.queryByText("scan.png")).not.toBeInTheDocument());
    expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
  });

  it("selects every row on the page and keeps documents still being processed", async () => {
    mockApi({ deleted: [listItem().id], in_progress: [second().id], not_found: [] });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    await userEvent.click(await screen.findByLabelText("Select all documents on this page"));
    await userEvent.click(screen.getByRole("button", { name: "Delete 2" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "1 document deleted. 1 document kept because it is being processed",
    );
    expect(await screen.findByRole("button", { name: "Delete 1" })).toBeInTheDocument();
    expect(screen.getByLabelText("Select scan.png")).toBeChecked();
  });

  it("does nothing when the confirmation is cancelled", async () => {
    const fetch = mockApi({ deleted: [], in_progress: [], not_found: [] });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    await userEvent.click(await screen.findByLabelText("Select scan.png"));
    await userEvent.click(screen.getByRole("button", { name: "Delete 1" }));

    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    expect(screen.getByLabelText("Select scan.png")).toBeChecked();
  });
});

it("compares the selected documents in the order they were ticked", async () => {
  mockList([
    listItem(),
    listItem({ id: "22222222-2222-2222-2222-222222222222", file_name: "export.xlsx" }),
    listItem({ id: "33333333-3333-3333-3333-333333333333", file_name: "scan.tiff" }),
  ]);
  renderPage();
  await screen.findByRole("link", { name: "invoice.pdf" });

  await userEvent.click(screen.getByLabelText("Select scan.tiff"));
  const compare = screen.getByRole("button", { name: "Compare 1" });
  expect(compare).toBeDisabled();
  expect(compare).toHaveAttribute("title", "Select at least two documents to compare.");
  await userEvent.click(screen.getByLabelText("Select invoice.pdf"));
  await userEvent.click(screen.getByRole("button", { name: "Compare 2" }));

  expect(location).toBe(
    "/compare?ids=33333333-3333-3333-3333-333333333333&ids=11111111-1111-1111-1111-111111111111",
  );
});

it("only compares processed documents", async () => {
  mockList([
    listItem(),
    listItem({
      id: "22222222-2222-2222-2222-222222222222",
      file_name: "new.csv",
      status: "uploaded",
      processing: summary({ status: "queued" }),
      invoice: null,
    }),
  ]);
  renderPage();
  await screen.findByRole("link", { name: "invoice.pdf" });

  await userEvent.click(screen.getByLabelText("Select all documents on this page"));

  const compare = screen.getByRole("button", { name: "Compare 2" });
  expect(compare).toBeDisabled();
  expect(compare.getAttribute("title")).toMatch(/Only processed documents.*new\.csv isn't/);
});
