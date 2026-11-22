import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import type { DocumentRead } from "../api/types";
import { DocumentViewer } from "./DocumentViewer";

const doc = (overrides: Partial<DocumentRead> = {}): DocumentRead => ({
  id: "d1",
  file_name: "invoice.pdf",
  file_type: "application/pdf",
  storage_path: "d1/invoice.pdf",
  status: "processed",
  size_bytes: 1234,
  sha256: null,
  created_at: "2026-11-01T10:00:00Z",
  updated_at: "2026-11-01T10:00:00Z",
  ...overrides,
});

function renderViewer(document: DocumentRead) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DocumentViewer document={document} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

it('says "No file found" when the stored original is missing', async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 404 }));

  renderViewer(doc());

  expect(await screen.findByText("No file found")).toBeInTheDocument();
  expect(screen.getByText("invoice.pdf")).toBeInTheDocument();
  expect(screen.queryByTitle("invoice.pdf")).not.toBeInTheDocument(); // no iframe
  expect(fetch).toHaveBeenCalledWith("/api/documents/d1/file", { method: "HEAD" });
});

it("shows the PDF when the file exists", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 200 }));

  renderViewer(doc());

  expect(await screen.findByTitle("invoice.pdf")).toHaveAttribute("src", "/api/documents/d1/file");
  expect(screen.queryByText("No file found")).not.toBeInTheDocument();
});

it("still tries to show the file when the check itself fails", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 503 }));

  renderViewer(doc({ file_type: "image/png", file_name: "scan.png" }));

  expect(await screen.findByAltText("scan.png")).toBeInTheDocument();
  expect(screen.queryByText("No file found")).not.toBeInTheDocument();
});
