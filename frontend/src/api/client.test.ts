import { ApiError, api } from "./client";

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

afterEach(() => vi.restoreAllMocks());

it("builds list queries from the given filters only", async () => {
  const fetch = mockFetch(200, { items: [], total: 0, offset: 0, limit: 25 });

  await api.listDocuments({ offset: 25, limit: 25, status: undefined, needs_review: true });

  expect(fetch).toHaveBeenCalledWith("/api/documents?offset=25&limit=25&needs_review=true", undefined);
});

it("raises the backend's detail message", async () => {
  mockFetch(415, { detail: "notes.txt: unsupported file type." });

  await expect(api.getDocument("x")).rejects.toEqual(
    new ApiError(415, "notes.txt: unsupported file type."),
  );
});

it("flattens FastAPI validation errors", async () => {
  mockFetch(422, {
    detail: [
      { loc: ["body", "invoice_date"], msg: "Input should be a valid date" },
      { loc: ["body", "total_amount"], msg: "Input should be a valid number" },
    ],
  });

  await expect(api.getDocument("x")).rejects.toThrow(
    "invoice_date: Input should be a valid date; total_amount: Input should be a valid number",
  );
});

it("falls back to the status code without a JSON body", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("gateway down", { status: 502 }));

  await expect(api.getDocument("x")).rejects.toThrow("Request failed (HTTP 502)");
});

it("sends corrections as JSON", async () => {
  const fetch = mockFetch(200, {});

  await api.correctInvoice("doc-1", { is_invoice: false } as never);

  expect(fetch).toHaveBeenCalledWith("/api/documents/doc-1/invoice", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_invoice: false }),
  });
});
