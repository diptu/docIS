import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ACCEPT, SupportedFormats } from "./SupportedFormats";

describe("SupportedFormats", () => {
  it("lists every format the backend accepts", () => {
    render(<SupportedFormats />);

    const list = screen.getByRole("list", { name: "Supported formats:" });
    const labels = within(list).getAllByRole("listitem").map((item) => item.textContent);
    expect(labels).toEqual(["PDF", "JPG", "PNG", "TIFF", "Excel", "CSV"]);
    expect(within(list).getByText("Excel")).toHaveAttribute(
      "title",
      "Excel workbooks (.xlsx, .xls)",
    );
  });

  it("accepts the same extensions in the file picker", () => {
    expect(ACCEPT).toBe(".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv");
  });
});
