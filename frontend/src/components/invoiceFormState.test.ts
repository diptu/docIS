import { invoice } from "../test/fixtures";
import { emptyLine, fromForm, parseAmount, toForm } from "./invoiceFormState";

describe("parseAmount", () => {
  it.each([
    ["1250.5", 1250.5],
    ["1,250.50", 1250.5],
    ["1250,50", 1250.5],
    ["1.250,50", 1250.5],
    [" 42 ", 42],
    ["-7.5", -7.5],
    ["", null],
    ["   ", null],
  ])("%j -> %j", (raw, expected) => {
    expect(parseAmount(raw)).toBe(expected);
  });

  it.each(["abc", "12a", "1..2", "€5"])("rejects %j", (raw) => {
    expect(parseAmount(raw)).toBeUndefined();
  });
});

describe("form conversion", () => {
  it("round-trips an invoice unchanged", () => {
    const data = invoice();
    expect(fromForm(toForm(data), data)).toEqual({ invoice: data, errors: {} });
  });

  it("turns blanks into nulls, trims text and upper-cases currency", () => {
    const data = invoice();
    const form = toForm(data);
    form.fields.payment_terms = "   ";
    form.fields["vendor.tax_id"] = "";
    form.fields.invoice_number = "  INV-9 ";
    form.fields.currency = "usd";
    form.fields.total_amount = "";

    const { invoice: result } = fromForm(form, data);

    expect(result).toMatchObject({
      payment_terms: null,
      invoice_number: "INV-9",
      currency: "USD",
      total_amount: null,
      vendor: { tax_id: null },
    });
  });

  it("collects errors for invalid numbers and dates", () => {
    const data = invoice();
    const form = toForm(data);
    form.fields.total_amount = "lots";
    form.fields.due_date = "03/12/2026";
    form.lines[0]!.quantity = "ten";

    expect(fromForm(form, data)).toEqual({
      invoice: null,
      errors: {
        total_amount: "Not a number",
        due_date: "Use YYYY-MM-DD",
        "line_items.0.quantity": "Not a number",
      },
    });
  });

  it("adds empty line items as all-null rows", () => {
    const data = invoice({ line_items: [] });
    const form = toForm(data);
    form.lines.push(emptyLine());

    expect(fromForm(form, data).invoice?.line_items).toEqual([
      {
        description: null,
        product_code: null,
        quantity: null,
        unit: null,
        unit_price: null,
        tax_rate: null,
        amount: null,
      },
    ]);
  });
});
