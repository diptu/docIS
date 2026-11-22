import { formatLimit, uploadProblem } from "./uploadLimits";

const limits = { max_file_size_bytes: 100, max_file_size_mb: 0.5, max_files_per_upload: 10 };
const sized = (name: string, size: number) => {
  const f = new File([""], name);
  Object.defineProperty(f, "size", { value: size });
  return f;
};

it("formats limits without trailing zeros", () => {
  expect(formatLimit(20)).toBe("20 MB");
  expect(formatLimit(0.5)).toBe("0.5 MB");
  expect(formatLimit(0.0625)).toBe("0.06 MB");
});

it("accepts files exactly at the limit", () => {
  expect(uploadProblem([sized("a", 100)], limits)).toBeNull();
});

it("names at most three oversized files", () => {
  const files = ["a", "b", "c", "d", "e"].map((n) => sized(n, 101));
  expect(uploadProblem(files, limits)).toBe("Larger than 0.5 MB: a, b, c and 2 more. Nothing was uploaded.");
});
