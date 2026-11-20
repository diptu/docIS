import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { UploadButton } from "./UploadButton";

const MB = 1024 * 1024;
const LIMITS = { max_file_size_bytes: 2 * MB, max_file_size_mb: 2, max_files_per_upload: 2 };

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <UploadButton />
    </QueryClientProvider>,
  );
}

function mockApi(limits: object | null = LIMITS) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => {
    if (String(url).endsWith("/documents/upload-limits"))
      return limits
        ? new Response(JSON.stringify(limits), { status: 200 })
        : new Response(JSON.stringify({ detail: "down" }), { status: 503 });
    if (init?.method === "POST") return new Response(JSON.stringify([{ id: "d1" }]), { status: 201 });
    throw new Error(`unexpected ${String(url)}`);
  });
}

const file = (name: string, size: number) => {
  const f = new File(["%PDF-1.7"], name, { type: "application/pdf" });
  Object.defineProperty(f, "size", { value: size });
  return f;
};

const posts = (fetch: ReturnType<typeof mockApi>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST");

afterEach(() => vi.restoreAllMocks());

it("shows the configured limits", async () => {
  mockApi();
  renderButton();

  expect(await screen.findByText("Up to 2 MB per file, 2 files per upload")).toBeInTheDocument();
});

it("refuses files over the size limit without uploading anything", async () => {
  const fetch = mockApi();
  renderButton();
  await screen.findByText(/Up to 2 MB/);

  await userEvent.upload(screen.getByLabelText("Choose files to upload"), [
    file("small.pdf", MB),
    file("huge.pdf", 3 * MB),
  ]);

  expect(screen.getByRole("alert")).toHaveTextContent("Larger than 2 MB: huge.pdf. Nothing was uploaded.");
  expect(posts(fetch)).toHaveLength(0);
});

it("refuses more files than one upload allows", async () => {
  const fetch = mockApi();
  renderButton();
  await screen.findByText(/Up to 2 MB/);

  await userEvent.upload(
    screen.getByLabelText("Choose files to upload"),
    ["a.pdf", "b.pdf", "c.pdf"].map((n) => file(n, 10)),
  );

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Too many files: 3 selected, the maximum is 2 per upload.",
  );
  expect(posts(fetch)).toHaveLength(0);
});

it("uploads files within the limits", async () => {
  const fetch = mockApi();
  renderButton();
  await screen.findByText(/Up to 2 MB/);

  await userEvent.upload(screen.getByLabelText("Choose files to upload"), [file("ok.pdf", 2 * MB)]);

  expect(await screen.findByText("1 file uploaded.")).toBeInTheDocument();
  expect(posts(fetch)).toHaveLength(1);
});

it("still uploads when the limits cannot be loaded (the server enforces them)", async () => {
  const fetch = mockApi(null);
  renderButton();

  await userEvent.upload(screen.getByLabelText("Choose files to upload"), [file("big.pdf", 50 * MB)]);

  expect(await screen.findByText("1 file uploaded.")).toBeInTheDocument();
  expect(posts(fetch)).toHaveLength(1);
  expect(screen.queryByText(/per file/)).not.toBeInTheDocument();
});
