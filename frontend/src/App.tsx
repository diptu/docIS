import { Link, Route, Routes } from "react-router";

import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { DocumentsPage } from "./pages/DocumentsPage";

export function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <Link to="/" className="text-lg font-bold tracking-tight text-slate-900">
            doc<span className="text-blue-600">IS</span>
          </Link>
          <nav className="text-sm">
            <Link to="/" className="text-slate-600 hover:text-slate-900">
              Documents
            </Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Routes>
          <Route path="/" element={<DocumentsPage />} />
          <Route path="/documents/:id" element={<DocumentDetailPage />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
