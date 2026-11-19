import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ValidationCheck } from "../api/types";
import { invoice } from "../test/fixtures";
import { InvoiceForm } from "./InvoiceForm";

const failedTotal: ValidationCheck = {
  code: "total",
  status: "failed",
  message: "Subtotal + tax vs total: expected 1487.5, invoice states 1500",
  expected: "1487.5",
  actual: "1500",
};

function setup(checks: ValidationCheck[] = [failedTotal]) {
  const onSave = vi.fn();
  render(
    <InvoiceForm invoice={invoice()} checks={checks} saving={false} saveError={null} onSave={onSave} />,
  );
  return { onSave, user: userEvent.setup() };
}

it("shows the extracted values", () => {
  setup();
  expect(screen.getByLabelText("Invoice number")).toHaveValue("INV-2026-001");
  expect(screen.getByLabelText("vendor Name")).toHaveValue("ACME GmbH");
  expect(screen.getByLabelText("Line 1 description")).toHaveValue("Consulting");
  expect(screen.getByLabelText("Total")).toHaveValue("1500");
});

it("saves a corrected total", async () => {
  const { onSave, user } = setup();
  const save = screen.getByRole("button", { name: "Save corrections" });
  expect(save).toBeDisabled(); // nothing changed yet

  const total = screen.getByLabelText("Total");
  await user.clear(total);
  await user.type(total, "1.487,50");
  await user.click(save);

  expect(onSave).toHaveBeenCalledOnce();
  expect(onSave.mock.calls[0]![0]).toEqual(invoice({ total_amount: 1487.5 }));
});

it("edits, adds and removes line items", async () => {
  const { onSave, user } = setup();

  await user.click(screen.getByRole("button", { name: "+ Add line" }));
  await user.type(screen.getByLabelText("Line 2 description"), "Travel");
  await user.type(screen.getByLabelText("Line 2 amount"), "80");
  await user.click(screen.getByRole("button", { name: "Remove line 1" }));
  await user.click(screen.getByRole("button", { name: "Save corrections" }));

  expect(onSave.mock.calls[0]![0].line_items).toEqual([
    {
      description: "Travel",
      product_code: null,
      quantity: null,
      unit: null,
      unit_price: null,
      tax_rate: null,
      amount: 80,
    },
  ]);
});

it("blocks saving invalid input and explains why", async () => {
  const { onSave, user } = setup();

  await user.clear(screen.getByLabelText("Total"));
  await user.type(screen.getByLabelText("Total"), "lots");
  await user.click(screen.getByRole("button", { name: "Save corrections" }));

  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByText("Not a number")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Fix the highlighted fields");
});

it("discards changes", async () => {
  const { user } = setup();
  await user.type(screen.getByLabelText("Invoice number"), "X");

  await user.click(screen.getByRole("button", { name: "Discard changes" }));

  expect(screen.getByLabelText("Invoice number")).toHaveValue("INV-2026-001");
});

it("marks uncertain fields and fields that failed a check", () => {
  setup();
  const markers = screen.getAllByTitle("Uncertain or failed a check");
  // invoice_date (model uncertain) and total_amount (failed check)
  expect(markers).toHaveLength(2);
});

it("shows the server's error message when saving fails", () => {
  render(
    <InvoiceForm
      invoice={invoice()}
      checks={[]}
      saving={false}
      saveError="invoice_date: Input should be a valid date"
      onSave={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Input should be a valid date");
});
