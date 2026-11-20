import { render } from "@testing-library/react";

import { Highlighted } from "./Highlighted";

const html = (text: string) => render(<Highlighted text={text} />).container.innerHTML;

it("marks highlighted terms", () => {
  expect(html("Total \u0002invoice\u0003 for \u0002ACME\u0003.")).toBe(
    '<span>Total </span><mark class="rounded-sm bg-amber-200/80 px-0.5 text-slate-900">invoice</mark>' +
      '<span> for </span><mark class="rounded-sm bg-amber-200/80 px-0.5 text-slate-900">ACME</mark><span>.</span>',
  );
});

it("renders plain and malformed text without markers", () => {
  expect(html("No matches here")).toBe("<span>No matches here</span>");
  expect(html("open \u0002never closed")).toBe("<span>open never closed</span>");
});

it("never interprets snippet text as HTML", () => {
  expect(html("<b>x</b>")).toBe("<span>&lt;b&gt;x&lt;/b&gt;</span>");
});
