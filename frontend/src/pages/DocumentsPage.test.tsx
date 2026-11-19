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

function mockList(items: ReturnType<typeof listItem>[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items, total: items.length, offset: 0, limit: 25 }), {
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
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ detail: "Database unavailable" }), { status: 503 }),
  );

  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable");
});
