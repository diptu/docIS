import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

import { listItem } from "../test/fixtures";
import { DocumentsPage } from "./DocumentsPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DocumentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const STATS = { total: 4, completed: 2, failed: 1, in_progress: 1, avg_processing_seconds: 125 };
const isStats = (url: unknown) => String(url).endsWith("/documents/stats");
const statsResponse = () => new Response(JSON.stringify(STATS), { status: 200 });

function mockList(items: ReturnType<typeof listItem>[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (url) =>
    isStats(url) ? statsResponse() : new Response(JSON.stringify({ items, total: items.length, offset: 0, limit: 25 }), {
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
      processing: { kind: "extract_content", status: "running", stage: "ocr" },
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
  expect(within(second!).getByText("Ocr…")).toBeInTheDocument();
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

it("shows load errors", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ detail: "Database unavailable" }), { status: 503 }),
  );

  renderPage();

  expect(
    await screen.findByText("Could not load documents: Database unavailable"),
  ).toBeInTheDocument();
  expect(
    await screen.findByText("Could not load statistics: Database unavailable"),
  ).toBeInTheDocument();
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
      if (isStats(url)) return statsResponse();
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
