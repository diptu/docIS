import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import type { CompareCell, CompareField, MultiComparison } from "../api/types";
import { ComparePage, compareUrl } from "./ComparePage";

const A = "11111111-1111-1111-1111-111111111111";
const B = "22222222-2222-2222-2222-222222222222";
const C = "33333333-3333-3333-3333-333333333333";

const present = (document_id: string, value: string, extra: Partial<CompareCell> = {}): CompareCell => ({
  document_id,
  presence: "present",
  value,
  normalized: value,
  matches_baseline: true,
  difference: null,
  ...extra,
});
const absent = (document_id: string, presence: "missing" | "unavailable"): CompareCell => ({
  document_id,
  presence,
  value: null,
  normalized: null,
  matches_baseline: null,
  difference: null,
});
const field = (overrides: Partial<CompareField> & Pick<CompareField, "field" | "label" | "cells">): CompareField => ({
  group: "header",
  kind: "text",
  status: "match",
  baseline_document_id: A,
  ...overrides,
});

const COMPARISON: MultiComparison = {
  documents: [
    { id: A, file_name: "invoice.pdf", file_type: "application/pdf", status: "processed", reference: true, invoice_available: true, unavailable_reason: null },
    { id: B, file_name: "invoice.csv", file_type: "text/csv", status: "processed", reference: false, invoice_available: true, unavailable_reason: null },
    { id: C, file_name: "export.xlsx", file_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", status: "processed", reference: false, invoice_available: false, unavailable_reason: "export.xlsx: no invoice has been extracted from it." },
  ],
  fields: [
    field({
      field: "invoice_number",
      label: "Invoice number",
      kind: "identifier",
      cells: [
        present(A, "INV-2026-001", { normalized: "inv2026001" }),
        present(B, "inv 2026 001", { normalized: "inv2026001" }),
        absent(C, "unavailable"),
      ],
    }),
    field({
      field: "due_date",
      label: "Due date",
      kind: "date",
      status: "missing",
      cells: [present(A, "2026-12-03"), absent(B, "missing"), absent(C, "unavailable")],
    }),
    field({
      field: "total_amount",
      label: "Total",
      group: "totals",
      kind: "money",
      status: "mismatch",
      cells: [
        present(A, "1487.50", { difference: "0.00" }),
        present(B, "1500.00", { matches_baseline: false, difference: "+12.50" }),
        absent(C, "unavailable"),
      ],
    }),
  ],
  line_items: [
    {
      position: 1,
      status: "mismatch",
      lines: [
        { document_id: A, line_number: 1, matched_by: "reference" },
        { document_id: B, line_number: 2, matched_by: "product_code" },
        { document_id: C, line_number: null, matched_by: null },
      ],
      fields: [
        field({ field: "description", label: "Description", group: "line", cells: [present(A, "Consulting"), present(B, "Consulting"), absent(C, "unavailable")] }),
        field({
          field: "quantity",
          label: "Quantity",
          group: "line",
          kind: "quantity",
          status: "mismatch",
          cells: [present(A, "10", { difference: "0" }), present(B, "10.1", { matches_baseline: false, difference: "+0.1" }), absent(C, "unavailable")],
        }),
      ],
    },
  ],
  field_counts: { match: 1, mismatch: 1, missing: 1, unavailable: 0 },
  line_counts: { match: 0, mismatch: 1, missing: 0, unavailable: 0 },
  notes: ["export.xlsx: no invoice has been extracted from it."],
};

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
          <Route path="/compare" element={<ComparePage />} />
        </Routes>
        <LocationSpy />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const mockCompare = (body: unknown = COMPARISON, status = 200) =>
  vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response(JSON.stringify(body), { status }));

const rowOf = (label: string) => screen.getByRole("rowheader", { name: label }).closest("tr")!;

afterEach(() => vi.restoreAllMocks());

it("requests the documents in order and shows a table with status, values and differences", async () => {
  const fetch = mockCompare();
  renderAt(compareUrl([A, B, C]));

  await screen.findByRole("table");
  expect(String(fetch.mock.calls[0]![0])).toBe(`/api/compare/documents?ids=${A}&ids=${B}&ids=${C}`);

  const header = within(screen.getAllByRole("row")[0]!).getAllByRole("columnheader");
  expect(header.map((h) => h.textContent)).toEqual([
    "Field",
    "Status",
    expect.stringMatching(/^invoice\.pdfPDFReference/),
    expect.stringMatching(/^invoice\.csvCSV/),
    expect.stringMatching(/^export\.xlsxXLSXNo invoice/),
  ]);

  const total = within(rowOf("Total"));
  expect(total.getByText("Mismatch")).toBeInTheDocument();
  expect(total.getByText("1500.00")).toBeInTheDocument();
  expect(total.getByText("Δ +12.50")).toBeInTheDocument();
  expect(total.getByText("(differs)", { exact: false })).toBeInTheDocument();
  expect(total.getByText("baseline")).toBeInTheDocument();
  expect(total.getByText("Not available")).toBeInTheDocument();

  const due = within(rowOf("Due date"));
  expect(due.getByText("Missing", { selector: "td" })).toBeInTheDocument();

  const number = rowOf("Invoice number");
  expect(within(number).getByText("inv 2026 001").closest("td")).toHaveAttribute(
    "title",
    "Compared as “inv2026001”",
  );

  const line = screen.getByRole("rowheader", { name: "1. Consulting" }).closest("tr")!;
  expect(within(line).getByText("Line 2")).toBeInTheDocument();
  expect(within(line).getByText(/matched by product code/)).toBeInTheDocument();
  expect(within(rowOf("Quantity")).getByText("Δ +0.1")).toBeInTheDocument();

  const summary = within(screen.getByRole("region", { name: "Invoice fields" }));
  expect(summary.getByText(/1 mismatch/)).toBeInTheDocument();
  expect(summary.getByText(/1 missing/)).toBeInTheDocument();
  expect(screen.getByText("export.xlsx: no invoice has been extracted from it.")).toBeInTheDocument();
});

it("can show only the differences", async () => {
  mockCompare();
  renderAt(compareUrl([A, B, C]));
  await screen.findByRole("table");

  await userEvent.click(screen.getByLabelText("Show differences only"));

  expect(screen.queryByRole("rowheader", { name: "Invoice number" })).not.toBeInTheDocument();
  expect(screen.queryByRole("rowheader", { name: "Description" })).not.toBeInTheDocument();
  expect(rowOf("Total")).toBeInTheDocument();
  expect(rowOf("Due date")).toBeInTheDocument();
  expect(rowOf("Quantity")).toBeInTheDocument();
});

it("changes the reference and removes documents through the URL", async () => {
  const fetch = mockCompare();
  renderAt(compareUrl([A, B, C]));
  await screen.findByRole("table");

  await userEvent.click(screen.getByRole("button", { name: "Use invoice.csv as reference" }));
  expect(location).toBe(compareUrl([B, A, C]));
  await vi.waitFor(() =>
    expect(String(fetch.mock.lastCall![0])).toBe(`/api/compare/documents?ids=${B}&ids=${A}&ids=${C}`),
  );

  await userEvent.click(screen.getByRole("button", { name: "Remove export.xlsx from comparison" }));
  expect(location).toBe(compareUrl([B, A]));
});

it("explains how to start when fewer than two documents are chosen", () => {
  const fetch = mockCompare();
  renderAt(compareUrl([A]));

  expect(screen.getByText(/Select two to 10 processed documents/)).toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it("shows why a comparison was refused", async () => {
  mockCompare({ detail: '"new.csv" has not finished processing, so can\'t be compared yet.' }, 409);
  renderAt(compareUrl([A, B]));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("We couldn't load the comparison.");
  expect(alert).toHaveTextContent("has not finished processing");
});
