import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { job } from "../test/fixtures";
import { ProcessingPanel } from "./ProcessingPanel";

const panel = (overrides: Parameters<typeof job>[0], onRetry = vi.fn()) =>
  render(<ProcessingPanel job={job(overrides)} onRetry={onRetry} retrying={false} retryError={null} />);

it("shows the running stage, page count, attempt and a progress bar", () => {
  panel({ status: "running", stage: "ocr", progress_current: 3, progress_total: 4, percent: 75, attempts: 2 });

  const status = screen.getByRole("region", { name: "Processing status" });
  expect(status).toHaveTextContent("Extract content:OCR(3/4)Attempt 2 of 3");
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "75");
});

it("explains a scheduled retry with the last error", () => {
  panel({
    status: "queued",
    attempts: 1,
    retry_scheduled: true,
    error: "Couldn't reach the AI service. We'll try again automatically.",
    next_run_at: null,
  });

  expect(screen.getByText("Retry scheduled")).toBeInTheDocument();
  expect(
    screen.getByText("Last attempt failed: Couldn't reach the AI service. We'll try again automatically."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
});

it("shows a final failure with its error and a retry button", async () => {
  const onRetry = vi.fn();
  panel(
    {
      kind: "extract_invoice",
      status: "failed",
      attempts: 3,
      error: "The AI service is temporarily unavailable. Click Retry to try again.",
    },
    onRetry,
  );

  expect(screen.getByRole("alert")).toHaveTextContent("Extract invoice failed after 3 attempts");
  expect(
    screen.getByText("The AI service is temporarily unavailable. Click Retry to try again."),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledOnce();
});

it("doesn't count attempts when it stopped at the first (retrying can't help)", () => {
  panel({
    kind: "extract_invoice",
    status: "failed",
    attempts: 1,
    error: "Invoice extraction is unavailable right now: the AI service account has run out of credits.",
  });

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent(/^Extract invoice failed\s*Retry/);
  expect(alert).not.toHaveTextContent("of 3");
  expect(alert).toHaveTextContent("run out of credits");
});

it("renders nothing once processing succeeded", () => {
  const { container } = panel({ status: "succeeded" });
  expect(container).toBeEmptyDOMElement();
});
