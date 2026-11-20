const START = "\u0002";
const END = "\u0003";

/** Render search snippet text, turning U+0002 … U+0003 spans into <mark>. */
export function Highlighted({ text }: { text: string }) {
  const parts: { text: string; mark: boolean }[] = [];
  let rest = text;
  while (rest) {
    const start = rest.indexOf(START);
    const end = start === -1 ? -1 : rest.indexOf(END, start + 1);
    if (start === -1 || end === -1) {
      parts.push({ text: rest.replaceAll(START, "").replaceAll(END, ""), mark: false });
      break;
    }
    if (start > 0) parts.push({ text: rest.slice(0, start), mark: false });
    parts.push({ text: rest.slice(start + 1, end), mark: true });
    rest = rest.slice(end + 1);
  }
  return (
    <>
      {parts.map((part, i) =>
        part.mark ? (
          <mark key={i} className="rounded-sm bg-amber-200/80 px-0.5 text-slate-900">
            {part.text}
          </mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}
