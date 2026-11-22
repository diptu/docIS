import { ApiError, NETWORK_ERROR, api, shouldRetry } from "./client";

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

it("explains a gateway error without a JSON body", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("gateway down", { status: 502 }));

  const error = await api.getDocument("x").catch((e: unknown) => e);

  expect(error).toBeInstanceOf(ApiError);
  expect((error as ApiError).message).toBe(
    "The service is temporarily unavailable. Please try again in a moment.",
  );
  expect((error as ApiError).transient).toBe(true);
});

it("treats a bare 500 (backend restarting behind the dev proxy) as transient", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 500 }));

  const error = (await api.listDocuments().catch((e: unknown) => e)) as ApiError;

  expect(error.status).toBe(500);
  expect(error.message).toBe("The service is temporarily unavailable. Please try again in a moment.");
  expect(error.transient).toBe(true);
});

it("keeps the backend's message for a 500 that has one, and does not retry it", async () => {
  mockFetch(500, { detail: "Internal server error." });

  const error = (await api.listDocuments().catch((e: unknown) => e)) as ApiError;

  expect(error.message).toBe("Internal server error.");
  expect(error.transient).toBe(false);
});

it("reports an unreachable server instead of a raw fetch error", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

  const error = (await api.listDocuments().catch((e: unknown) => e)) as ApiError;

  expect(error.status).toBe(NETWORK_ERROR);
  expect(error.message).toBe("Can't connect to the server. Check your connection and try again.");
  expect(error.transient).toBe(true);
});

it("retries transient errors a few times and others once", () => {
  const transient = new ApiError(503, "down", true);
  const permanent = new ApiError(404, "missing");

  expect([0, 4].map((n) => shouldRetry(n, transient))).toEqual([true, true]);
  expect(shouldRetry(5, transient)).toBe(false);
  expect(shouldRetry(0, permanent)).toBe(true);
  expect(shouldRetry(1, permanent)).toBe(false);
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
