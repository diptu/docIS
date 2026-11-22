import { formatDuration } from "./format";

it.each([
  [null, "—"],
  [0.42, "0.4 s"],
  [12.34, "12.3 s"],
  [59.94, "59.9 s"],
  [59.96, "1m 00s"],
  [60, "1m 00s"],
  [125.4, "2m 05s"],
  [3599.6, "1h 00m"],
  [3720, "1h 02m"],
])("formatDuration(%s) is %s", (seconds, expected) => {
  expect(formatDuration(seconds)).toBe(expected);
});
