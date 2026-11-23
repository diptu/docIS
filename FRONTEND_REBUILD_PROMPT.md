Act as a Senior Frontend Engineer and rebuild the **docIS frontend** inside the existing `frontend/` directory. Work directly in the repository without modifying anything outside this directory. Make sensible decisions without asking questions.

Use **React 19, TypeScript (strict), Vite, Tailwind CSS v4, TanStack Query v5, React Router v8, Vitest, and React Testing Library**. Follow the existing project requirements and keep dependencies minimal.

Build a clean, responsive SaaS dashboard with three main pages:

1. **Documents:** Statistics, document listing, uploads, filters, pagination, selection, and bulk deletion.
2. **Document Intelligence:** Document preview, extracted invoice data, editable fields, validation results, OCR text, spreadsheet data, processing status, and correction history.
3. **Search:** Keyword, semantic, and hybrid search with highlighted results and document references.

Integrate with the existing FastAPI backend through `/api`. Follow its actual API contracts and implement proper loading, error handling, polling, and cache invalidation.

Keep the architecture modular (`api/`, `components/`, `pages/`, `lib/`, and `test/`). Use reusable components, accessible interactions, Tailwind utilities, and inline SVG icons. Follow a professional light theme with neutral slate colors and clear status indicators.

Write meaningful tests covering API behavior, utilities, forms, components, document management, and search. Use realistic fixtures and mock network requests.

**Before finishing, verify:**
- `npm install`
- `npm run typecheck`
- `npm test`
- `npm run build`
- All three pages work with the existing backend.
- No unexpected browser console errors.

Fix issues wherever possible. Never claim a check passed unless it actually did. Report any blockers clearly.

**Final report:** Summarize implemented features, project structure, test results, build status, and outstanding issues.

**Important:** Do not modify the root `.gitignore`. Its existing `lib/` rule may exclude `frontend/src/lib/`. Mention this separately in the final report.