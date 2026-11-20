import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import type { SearchHit } from "../api/types";
import { SearchPage } from "./SearchPage";

const hit = (overrides: Partial<SearchHit> = {}): SearchHit => ({
  document: {
    id: "11111111-1111-1111-1111-111111111111",
    file_name: "invoice.pdf",
    file_type: "application/pdf",
    status: "processed",
  },
  score: 1.2,
  matched: ["file_name", "keyword"],
  snippets: [{ text: "Total \u0002invoice\u0003 amount", page_number: 2, sheet_name: null }],
  ...overrides,
});

let location = "";
function LocationSpy() {
  const { pathname, search } = useLocation();
  location = pathname + search;
  return null;
}

function renderAt(url: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
        </Routes>
        <LocationSpy />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockSearch(hits: SearchHit[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const params = new URL(String(url), "http://x").searchParams;
    return new Response(
      JSON.stringify({ query: params.get("q"), mode: params.get("mode"), hits }),
      { status: 200 },
    );
  });
}

afterEach(() => vi.restoreAllMocks());

it("prompts for a query and does not search without one", () => {
  const fetch = mockSearch([]);
  renderAt("/search");

  expect(screen.getByText(/Enter a word, phrase or description/)).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it("searches by keyword and shows highlighted snippets", async () => {
  const fetch = mockSearch([hit()]);
  renderAt("/search");

  await userEvent.type(screen.getByLabelText("Search text"), "  invoice {Enter}");

  expect(await screen.findByRole("status")).toHaveTextContent("1 document found");
  expect(fetch).toHaveBeenCalledWith("/api/search?q=invoice&mode=keyword&limit=20", undefined);
  expect(location).toBe("/search?q=invoice&mode=keyword");
  const result = screen.getByRole("region", { name: "Search results" });
  expect(within(result).getByRole("link", { name: "invoice.pdf" })).toHaveAttribute(
    "href",
    "/documents/11111111-1111-1111-1111-111111111111",
  );
  expect(within(result).getByText("Page 2")).toBeInTheDocument();
  expect(within(result).getByText("invoice", { selector: "mark" })).toBeInTheDocument();
  expect(within(result).getByText("File name")).toBeInTheDocument();
});

it("switches to semantic search for the same query", async () => {
  const fetch = mockSearch([
    hit({
      matched: ["semantic"],
      snippets: [{ text: "Advisory services", page_number: null, sheet_name: "Q3" }],
    }),
  ]);
  renderAt("/search?q=consulting&mode=keyword");
  await screen.findByRole("status");

  await userEvent.click(screen.getByRole("radio", { name: "Semantic" }));

  await vi.waitFor(() =>
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/search?q=consulting&mode=semantic&limit=20",
      undefined,
    ),
  );
  expect(location).toBe("/search?q=consulting&mode=semantic");
  expect(await screen.findByText("Meaning")).toBeInTheDocument();
  expect(screen.getByText("Sheet “Q3”")).toBeInTheDocument();
});

it("reports no matches and errors", async () => {
  mockSearch([]);
  renderAt("/search?q=zebra&mode=hybrid");
  expect(await screen.findByRole("status")).toHaveTextContent("No documents match “zebra”.");
  expect(screen.getByRole("radio", { name: "Hybrid" })).toBeChecked();

  vi.restoreAllMocks();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ detail: "Semantic search is disabled" }), { status: 503 }),
  );
  renderAt("/search?q=x&mode=semantic");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Search failed: Semantic search is disabled",
  );
});
