import { useEffect, useMemo, useState, type ReactNode } from "react";

import type { DocumentType, InvoiceData, ValidationCheck } from "../api/types";
import {
  CHECK_FIELDS,
  emptyLine,
  fromForm,
  toForm,
  type InvoiceForm as Form,
  type LineForm,
} from "./invoiceFormState";

const DOCUMENT_TYPES: [DocumentType, string][] = [
  ["invoice", "Invoice"],
  ["credit_note", "Credit note"],
  ["other", "Not an invoice"],
];

interface Props {
  invoice: InvoiceData;
  checks: ValidationCheck[];
  saving: boolean;
  saveError: string | null;
  onSave: (invoice: InvoiceData) => void;
}

const LABELS: Record<string, string> = {
  invoice_number: "Invoice number",
  invoice_date: "Invoice date",
  due_date: "Due date",
  currency: "Currency",
  purchase_order_number: "PO number",
  payment_terms: "Payment terms",
  name: "Name",
  address: "Address",
  tax_id: "Tax ID",
  email: "Email",
  phone: "Phone",
  subtotal: "Subtotal",
  tax_amount: "Tax",
  total_amount: "Total",
  amount_due: "Amount due",
};

export function InvoiceForm({ invoice, checks, saving, saveError, onSave }: Props) {
  const initial = useMemo(() => toForm(invoice), [invoice]);
  const [form, setForm] = useState<Form>(initial);
  const [errors, setErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    setForm(initial);
    setErrors({});
  }, [initial]);

  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const flagged = new Set<string>(invoice.uncertain_fields);
  for (const check of checks) {
    if (check.status === "failed") for (const f of CHECK_FIELDS[check.code] ?? []) flagged.add(f);
  }

  const setField = (path: string, value: string) =>
    setForm((current) => ({ ...current, fields: { ...current.fields, [path]: value } }));
  const setLine = (index: number, key: keyof LineForm, value: string) =>
    setForm((current) => ({
      ...current,
      lines: current.lines.map((line, i) => (i === index ? { ...line, [key]: value } : line)),
    }));

  const field = (path: string, type: "text" | "date" = "text", wide = false) => {
    const top = path.split(".")[0] ?? path;
    const label = LABELS[path.split(".").pop() ?? path] ?? path;
    return (
      <Field
        key={path}
        label={label}
        error={errors[path]}
        flagged={flagged.has(top) || flagged.has(path)}
        wide={wide}
      >
        <input
          aria-label={path.includes(".") ? `${top} ${label}` : label}
          type={type}
          value={form.fields[path] ?? ""}
          onChange={(e) => setField(path, e.target.value)}
          className={inputClass(errors[path])}
          inputMode={type === "text" && path in AMOUNT_HINT ? "decimal" : undefined}
        />
      </Field>
    );
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const result = fromForm(form, invoice);
    setErrors(result.errors);
    if (result.invoice) onSave(result.invoice);
  };

  return (
    <form onSubmit={submit} className="space-y-6" noValidate>
      <div className="space-y-1">
        <label className="flex items-center gap-2 text-sm">
          Document type
          <select
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={form.document_type}
            onChange={(e) => setForm({ ...form, document_type: e.target.value as DocumentType })}
          >
            {DOCUMENT_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {form.document_type === "credit_note" && (
          <p className="text-xs text-slate-500">
            Enter credited amounts as positive numbers, even if the document prints them as −100.00, (100.00)
            or 100.00 CR. Use a negative amount only for a line that reduces the credit, such as a restocking fee.
          </p>
        )}
      </div>

      <Section title="Invoice">
        {field("invoice_number")}
        {field("invoice_date", "date")}
        {field("due_date", "date")}
        {field("currency")}
        {field("purchase_order_number")}
        {field("payment_terms")}
      </Section>

      {(["vendor", "customer"] as const).map((party) => (
        <Section key={party} title={party === "vendor" ? "Vendor" : "Customer (bill to)"} flagged={flagged.has(party)}>
          {field(`${party}.name`)}
          {field(`${party}.tax_id`)}
          {field(`${party}.address`, "text", true)}
          {field(`${party}.email`)}
          {field(`${party}.phone`)}
        </Section>
      ))}

      <fieldset className={sectionClass(flagged.has("line_items"))}>
        <legend className="px-1 text-sm font-semibold text-slate-700">Line items</legend>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="pb-1 pr-2 font-medium">Description</th>
                <th className="pb-1 pr-2 font-medium">Code</th>
                <th className="pb-1 pr-2 font-medium">Qty</th>
                <th className="pb-1 pr-2 font-medium">Unit</th>
                <th className="pb-1 pr-2 font-medium">Unit price</th>
                <th className="pb-1 pr-2 font-medium">Tax %</th>
                <th className="pb-1 pr-2 font-medium">Amount</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {form.lines.map((line, i) => (
                <tr key={i}>
                  {(
                    [
                      ["description", "w-56"],
                      ["product_code", "w-24"],
                      ["quantity", "w-20"],
                      ["unit", "w-16"],
                      ["unit_price", "w-24"],
                      ["tax_rate", "w-16"],
                      ["amount", "w-28"],
                    ] as const
                  ).map(([key, width]) => {
                    const path = `line_items.${i}.${key}`;
                    return (
                      <td key={key} className="pb-1 pr-2 align-top">
                        <input
                          aria-label={`Line ${i + 1} ${key.replace("_", " ")}`}
                          value={line[key]}
                          onChange={(e) => setLine(i, key, e.target.value)}
                          className={`${inputClass(errors[path])} ${width}`}
                        />
                        {errors[path] && <p className="text-xs text-red-700">{errors[path]}</p>}
                      </td>
                    );
                  })}
                  <td className="pb-1 align-top">
                    <button
                      type="button"
                      aria-label={`Remove line ${i + 1}`}
                      onClick={() =>
                        setForm({ ...form, lines: form.lines.filter((_, j) => j !== i) })
                      }
                      className="rounded px-2 py-1 text-slate-400 hover:bg-red-50 hover:text-red-700"
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button
          type="button"
          onClick={() => setForm({ ...form, lines: [...form.lines, emptyLine()] })}
          className="mt-2 text-sm font-medium text-blue-700 hover:text-blue-900"
        >
          + Add line
        </button>
      </fieldset>

      <Section title="Totals">
        {field("subtotal")}
        {field("tax_amount")}
        {field("total_amount")}
        {field("amount_due")}
      </Section>

      <div className="sticky bottom-0 flex items-center justify-end gap-3 border-t border-slate-200 bg-white/95 py-3">
        {Object.keys(errors).length > 0 && (
          <p role="alert" className="mr-auto text-sm text-red-700">
            Fix the highlighted fields before saving.
          </p>
        )}
        {saveError && (
          <p role="alert" className="mr-auto text-sm text-red-700">
            {saveError}
          </p>
        )}
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => {
            setForm(initial);
            setErrors({});
          }}
          className="rounded-md px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-40"
        >
          Discard changes
        </button>
        <button
          type="submit"
          disabled={!dirty || saving}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500 disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save corrections"}
        </button>
      </div>
    </form>
  );
}

const AMOUNT_HINT = { subtotal: 1, tax_amount: 1, total_amount: 1, amount_due: 1 };

const inputClass = (error?: string) =>
  `w-full rounded-md border px-2 py-1.5 text-sm shadow-sm focus:outline-none focus:ring-2 ${
    error
      ? "border-red-400 focus:ring-red-300"
      : "border-slate-300 focus:border-blue-500 focus:ring-blue-200"
  }`;

const sectionClass = (flagged: boolean) =>
  `rounded-lg border p-3 ${flagged ? "border-amber-300 bg-amber-50/40" : "border-slate-200"}`;

function Section({
  title,
  flagged = false,
  children,
}: {
  title: string;
  flagged?: boolean;
  children: ReactNode;
}) {
  return (
    <fieldset className={sectionClass(flagged)}>
      <legend className="px-1 text-sm font-semibold text-slate-700">{title}</legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

function Field({
  label,
  error,
  flagged,
  wide,
  children,
}: {
  label: string;
  error?: string;
  flagged: boolean;
  wide: boolean;
  children: ReactNode;
}) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <div className="mb-1 flex items-center gap-1 text-xs font-medium text-slate-600">
        {label}
        {flagged && (
          <span title="Uncertain or failed a check" className="text-amber-600">
            ●
          </span>
        )}
      </div>
      {children}
      {error && <p className="mt-0.5 text-xs text-red-700">{error}</p>}
    </div>
  );
}
