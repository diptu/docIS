// Form state for editing an InvoiceData. Inputs hold raw strings so partial
// input ("12.") is never rejected mid-typing; conversion happens on save.

import type { InvoiceData, InvoiceLineItem, Party } from "../api/types";

export const TEXT_FIELDS = [
  "invoice_number",
  "currency",
  "purchase_order_number",
  "payment_terms",
] as const;
export const DATE_FIELDS = ["invoice_date", "due_date"] as const;
export const AMOUNT_FIELDS = ["subtotal", "tax_amount", "total_amount", "amount_due"] as const;
export const PARTY_FIELDS = ["name", "address", "tax_id", "email", "phone"] as const;
export const LINE_TEXT = ["description", "product_code", "unit"] as const;
export const LINE_NUMBERS = ["quantity", "unit_price", "tax_rate", "amount"] as const;

type LineKey = (typeof LINE_TEXT)[number] | (typeof LINE_NUMBERS)[number];
export type LineForm = Record<LineKey, string>;

export interface InvoiceForm {
  is_invoice: boolean;
  fields: Record<string, string>; // "invoice_number", "vendor.name", ...
  lines: LineForm[];
}

const str = (value: string | number | null): string => (value === null ? "" : String(value));

export function toForm(invoice: InvoiceData): InvoiceForm {
  const fields: Record<string, string> = {};
  for (const key of [...TEXT_FIELDS, ...DATE_FIELDS, ...AMOUNT_FIELDS]) {
    fields[key] = str(invoice[key]);
  }
  for (const party of ["vendor", "customer"] as const) {
    for (const key of PARTY_FIELDS) fields[`${party}.${key}`] = str(invoice[party][key]);
  }
  return {
    is_invoice: invoice.is_invoice,
    fields,
    lines: invoice.line_items.map(lineToForm),
  };
}

function lineToForm(item: InvoiceLineItem): LineForm {
  return {
    description: str(item.description),
    product_code: str(item.product_code),
    unit: str(item.unit),
    quantity: str(item.quantity),
    unit_price: str(item.unit_price),
    tax_rate: str(item.tax_rate),
    amount: str(item.amount),
  };
}

export const emptyLine = (): LineForm => lineToForm({
  description: null,
  product_code: null,
  unit: null,
  quantity: null,
  unit_price: null,
  tax_rate: null,
  amount: null,
});

const text = (value: string): string | null => value.trim() || null;

/** Parse "1250.5", "1,250.50", "1250,50", "1.250,50" into a number. */
export function parseAmount(raw: string): number | null | undefined {
  let value = raw.trim().replace(/\s/g, "");
  if (!value) return null;
  const lastComma = value.lastIndexOf(",");
  const lastDot = value.lastIndexOf(".");
  if (lastComma > lastDot) {
    // Comma is the decimal separator: drop dots, turn the comma into a dot.
    value = value.replace(/\./g, "").replace(",", ".");
  } else {
    value = value.replace(/,/g, "");
  }
  if (!/^-?\d+(\.\d+)?$/.test(value)) return undefined; // invalid
  return Number(value);
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

export interface ConversionResult {
  invoice: InvoiceData | null;
  errors: Record<string, string>; // field path -> message
}

/** Convert the form back to InvoiceData, collecting field errors. */
export function fromForm(form: InvoiceForm, original: InvoiceData): ConversionResult {
  const errors: Record<string, string> = {};
  const f = form.fields;
  const amount = (path: string, raw: string): number | null => {
    const value = parseAmount(raw);
    if (value === undefined) {
      errors[path] = "Not a number";
      return null;
    }
    return value;
  };
  const date = (path: string): string | null => {
    const value = text(f[path] ?? "");
    if (value !== null && !DATE.test(value)) errors[path] = "Use YYYY-MM-DD";
    return value;
  };
  const party = (prefix: "vendor" | "customer"): Party => ({
    name: text(f[`${prefix}.name`] ?? ""),
    address: text(f[`${prefix}.address`] ?? ""),
    tax_id: text(f[`${prefix}.tax_id`] ?? ""),
    email: text(f[`${prefix}.email`] ?? ""),
    phone: text(f[`${prefix}.phone`] ?? ""),
  });

  const invoice: InvoiceData = {
    is_invoice: form.is_invoice,
    invoice_number: text(f.invoice_number ?? ""),
    invoice_date: date("invoice_date"),
    due_date: date("due_date"),
    currency: text(f.currency ?? "")?.toUpperCase() ?? null,
    purchase_order_number: text(f.purchase_order_number ?? ""),
    payment_terms: text(f.payment_terms ?? ""),
    vendor: party("vendor"),
    customer: party("customer"),
    line_items: form.lines.map((line, i) => ({
      description: text(line.description),
      product_code: text(line.product_code),
      unit: text(line.unit),
      quantity: amount(`line_items.${i}.quantity`, line.quantity),
      unit_price: amount(`line_items.${i}.unit_price`, line.unit_price),
      tax_rate: amount(`line_items.${i}.tax_rate`, line.tax_rate),
      amount: amount(`line_items.${i}.amount`, line.amount),
    })),
    subtotal: amount("subtotal", f.subtotal ?? ""),
    tax_amount: amount("tax_amount", f.tax_amount ?? ""),
    total_amount: amount("total_amount", f.total_amount ?? ""),
    amount_due: amount("amount_due", f.amount_due ?? ""),
    uncertain_fields: original.uncertain_fields,
  };
  return { invoice: Object.keys(errors).length ? null : invoice, errors };
}

/** Which form fields each failed amount check concerns. */
export const CHECK_FIELDS: Record<string, string[]> = {
  line_amounts: ["line_items"],
  lines_subtotal: ["subtotal", "line_items"],
  tax_amount: ["tax_amount"],
  total: ["total_amount"],
  amount_due: ["amount_due"],
};
