import { useState } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from "react-router";

import { SearchIcon } from "./components/SearchIcon";
import { ComparePage } from "./pages/ComparePage";
import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { ProcessingPage } from "./pages/ProcessingPage";
import { SearchPage } from "./pages/SearchPage";

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? "font-medium text-slate-900" : "text-slate-600 hover:text-slate-900";

/** Header search box; hidden on the search page, which has its own. */
function HeaderSearch() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [q, setQ] = useState("");
  if (pathname === "/search") return null;
  return (
    <form
      role="search"
      className="relative ml-auto w-full max-w-xs"
      onSubmit={(e) => {
        e.preventDefault();
        const trimmed = q.trim();
        if (!trimmed) return;
        void navigate(`/search?${new URLSearchParams({ q: trimmed, mode: "keyword" })}`);
        setQ("");
      }}
    >
      <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-slate-400">
        <SearchIcon />
      </span>
      <input
        type="search"
        aria-label="Search documents"
        placeholder="Search documents…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="w-full rounded-md border border-slate-300 bg-slate-50 py-1.5 pl-8 pr-3 text-sm focus:border-blue-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/30"
      />
    </form>
  );
}

export function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <Link to="/" className="text-lg font-bold tracking-tight text-slate-900">
            doc<span className="text-blue-600">IS</span>
          </Link>
          <nav className="flex gap-4 text-sm">
            <NavLink to="/" end className={navClass}>
              Documents
            </NavLink>
            <NavLink to="/processing" className={navClass}>
              Processing
            </NavLink>
            <NavLink to="/compare" className={navClass}>
              Compare
            </NavLink>
            <NavLink to="/search" className={navClass}>
              Search
            </NavLink>
          </nav>
          <HeaderSearch />
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<DocumentsPage />} />
          <Route path="/documents/:id" element={<DocumentDetailPage />} />
          <Route path="/processing" element={<ProcessingPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
