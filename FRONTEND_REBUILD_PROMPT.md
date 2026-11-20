# Prompt: rebuild the docIS frontend

# Build the docIS Frontend

Act as a **Senior Frontend Engineer** experienced in React, TypeScript, Next.js/Vite, Tailwind CSS, and modern frontend architecture.

Build a complete, production-quality frontend for **docIS**, a document intelligence platform that allows users to upload invoices, PDFs, scanned images, and spreadsheets, then review, search, validate, correct, and manage extracted information.

Work directly in the repository. inside `frontend/` directory and implement the application without modifying anything outside it. Do not ask questions; make sensible decisions and best practices based on the requirements below.

### 1. Tech Stack

Use the following stack:

- React 19 + TypeScript (strict mode)
- Vite
- Tailwind CSS v4
- TanStack Query v5
- React Router v8 (`react-router`)
- Vitest + React Testing Library + jsdom

Keep dependencies minimal. Avoid UI libraries, icon packages, and unnecessary state management.

Use the exact dependency versions and configuration specified in the original project requirements.

### 2. Architecture

Keep the code modular, maintainable, and easy to extend.

Organize the application into:

- `api/` – API client, types, and TanStack Query hooks.
- `components/` – Reusable UI components and document-related components.
- `pages/` – Documents, document details, and search pages.
- `lib/` – Formatting and shared utilities.
- `test/` – Shared fixtures and test setup.

Use named exports, strict TypeScript, reusable components, and clear separation of concerns.

### 3. Backend Integration

Integrate with the existing FastAPI backend through `/api`.

Implement the API client and query hooks for:

- Document listing, uploading, and deletion.
- Document statistics and processing status.
- Invoice extraction and correction.
- Validation errors and correction history.
- PDF page text and spreadsheet data.
- Keyword, semantic, and hybrid search.

Follow the existing backend response schemas and endpoint contracts. Handle loading, errors, retries, polling, and cache invalidation appropriately.

### 4. Application Pages

Build three main pages:

1. **Documents:** Overview statistics, document table, status/review filters, pagination, file uploads, selection, and bulk deletion.
2. **Document Intelligence:** Document preview, extracted invoice information, editable invoice fields, validation results, review issues, processing status, OCR text, spreadsheet data, and correction history.
3. **Search:** Keyword, semantic, and hybrid search with highlighted matching text, document links, and page/sheet references.

Use the detailed UI specifications and exact accessibility labels, messages, and behaviors provided in the original requirements.

### 5. UI & Design

Follow a clean, professional SaaS dashboard design.

- Light theme with a slate-based neutral palette.
- Blue primary actions, with red, amber, and emerald for status indicators.
- Consistent spacing, typography, borders, and card layouts.
- Responsive layouts for desktop and smaller screens.
- Accessible forms, buttons, navigation, and keyboard interactions.
- Clear loading, empty, success, and error states.

Use Tailwind utility classes only and inline SVGs for icons.

### 6. Testing & Quality

Implement tests using Vitest and React Testing Library.

Cover API behavior, formatting utilities, invoice form logic, component interactions, document management, and search workflows.

Use realistic fixtures and mock API requests at the network boundary. Test observable behavior rather than implementation details.

### 7. Acceptance Criteria

Do not consider the work complete until:

- `npm install` succeeds.
- `npm run typecheck` passes.
- `npm test` passes.
- `npm run build` succeeds.
- The application runs against the existing FastAPI backend.
- All three pages render correctly with real backend data.
- No unexpected browser console errors occur.

If a check cannot be completed because of an unavailable dependency or backend service, clearly report it rather than claiming success.

### Final Report

Summarize the implemented features, project structure, test count, build results, and any outstanding issues.

**Important:** Do not modify the root `.gitignore`. It contains a `lib/` rule that may exclude `frontend/src/lib/`. Mention this in the final report so it can be addressed separately.