import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { useSearch } from "../api/hooks";
import type { SearchHit, SearchMode, SearchSnippet } from "../api/types";
import { Badge, STATUS_TONE } from "../components/Badge";
import { Highlighted } from "../components/Highlighted";
import { SearchIcon } from "../components/SearchIcon";
import { FILE_TYPE_LABELS, humanize } from "../lib/format";

const MODES: { value: SearchMode; label: string; hint: string }[] = [
  {
    value: "keyword",
    label: "Keyword",
    hint: 'Matches words (and their forms). Use "exact phrase", or, -exclude.',
  },
  {
    value: "semantic",
    label: "Semantic",
    hint: 'Matches meaning: "unpaid consulting" finds "advisory services due".',
  },
  { value: "hybrid", label: "Hybrid", hint: "Keyword and semantic results, ranked together." },
];

const MATCH_LABELS: Record<SearchHit["matched"][number], string> = {
  keyword: "Keyword",
  semantic: "Meaning",
  file_name: "File name",
};

export const isSearchMode = (value: string | null): value is SearchMode =>
  MODES.some((m) => m.value === value);

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const modeParam = params.get("mode");
  const mode: SearchMode = isSearchMode(modeParam) ? modeParam : "keyword";
  const [draft, setDraft] = useState(q);
  // Follow the URL when it changes elsewhere (header search, back button).
  useEffect(() => setDraft(q), [q]);

  const search = useSearch(q, mode);
  const go = (nextQ: string, nextMode: SearchMode) => {
    const trimmed = nextQ.trim();
    setParams(trimmed ? { q: trimmed, mode: nextMode } : { mode: nextMode });
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Search</h1>
        <p className="text-sm text-slate-600">
          Search the text of every document (pages, scans and spreadsheet cells) and file names.
        </p>
      </div>

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          go(draft, mode);
        }}
        className="space-y-3"
      >
        <div className="flex gap-2">
          <label className="relative flex-1">
            <span className="sr-only">Search text</span>
            <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-400">
              <SearchIcon />
            </span>
            <input
              type="search"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={mode === "semantic" ? "Describe what you're looking for…" : "Search documents…"}
              maxLength={500}
              autoFocus
              className="w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
            />
          </label>
          <button
            type="submit"
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-500"
          >
            Search
          </button>
        </div>

        <fieldset className="flex flex-wrap items-center gap-3">
          <legend className="sr-only">Search mode</legend>
          <div className="inline-flex rounded-md border border-slate-300 bg-white p-0.5 text-sm shadow-sm">
            {MODES.map((m) => (
              <label
                key={m.value}
                className={`cursor-pointer rounded px-3 py-1 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-blue-500/40 ${
                  mode === m.value ? "bg-blue-600 font-medium text-white" : "text-slate-700 hover:bg-slate-100"
                }`}
              >
                <input
                  type="radio"
                  name="mode"
                  value={m.value}
                  checked={mode === m.value}
                  onChange={() => go(draft, m.value)}
                  className="sr-only"
                />
                {m.label}
              </label>
            ))}
          </div>
          <p className="text-xs text-slate-500">{MODES.find((m) => m.value === mode)?.hint}</p>
        </fieldset>
      </form>

      <Results q={q} search={search} />
    </div>
  );
}

function Results({ q, search }: { q: string; search: ReturnType<typeof useSearch> }) {
  if (!q)
    return (
      <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-10 text-center text-sm text-slate-500">
        Enter a word, phrase or description to search your documents.
      </p>
    );
  if (search.isPending) return <p className="text-sm text-slate-500">Searching…</p>;
  if (search.isError)
    return (
      <p role="alert" className="text-sm text-red-700">
        Search failed: {search.error.message}
      </p>
    );

  const hits = search.data.hits;
  return (
    <section aria-label="Search results" className={`space-y-3 ${search.isFetching ? "opacity-60" : ""}`}>
      <p role="status" className="text-sm text-slate-600">
        {hits.length === 0
          ? `No documents match “${search.data.query}”.`
          : `${hits.length} document${hits.length === 1 ? "" : "s"} found`}
      </p>
      <ul className="space-y-3">
        {hits.map((hit) => (
          <Hit key={hit.document.id} hit={hit} />
        ))}
      </ul>
    </section>
  );
}

function location(snippet: SearchSnippet): string | null {
  if (snippet.page_number !== null) return `Page ${snippet.page_number}`;
  if (snippet.sheet_name !== null) return `Sheet “${snippet.sheet_name}”`;
  return null;
}

function Hit({ hit }: { hit: SearchHit }) {
  const { document: doc } = hit;
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/documents/${doc.id}`} className="font-medium text-blue-700 hover:underline">
          {doc.file_name}
        </Link>
        <span className="text-xs text-slate-500">{FILE_TYPE_LABELS[doc.file_type] ?? doc.file_type}</span>
        <Badge tone={STATUS_TONE[doc.status]}>{humanize(doc.status)}</Badge>
        <span className="ml-auto flex gap-1" aria-label="Matched by">
          {hit.matched.map((m) => (
            <Badge key={m} tone={m === "semantic" ? "blue" : "gray"}>
              {MATCH_LABELS[m] ?? humanize(m)}
            </Badge>
          ))}
        </span>
      </div>
      {hit.snippets.length > 0 && (
        <ul className="mt-2 space-y-2">
          {hit.snippets.map((snippet, i) => (
            <li key={i} className="border-l-2 border-slate-200 pl-3 text-sm text-slate-700">
              {location(snippet) && (
                <span className="block text-xs font-medium text-slate-500">{location(snippet)}</span>
              )}
              <span className="line-clamp-3 whitespace-pre-line">
                <Highlighted text={snippet.text} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
