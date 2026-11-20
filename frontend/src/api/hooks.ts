import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api, type JobListParams, type ListParams } from "./client";
import type { InvoiceData, SearchMode } from "./types";

const ACTIVE = new Set(["queued", "running"]);
const POLL_MS = 2000;

/** 404 means "not there yet" for invoices; don't retry or treat as an error. */
const notFoundIsEmpty = <T>(fetcher: () => Promise<T>) => async (): Promise<T | null> => {
  try {
    return await fetcher();
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
};

export function useDocuments(params: ListParams) {
  return useQuery({
    queryKey: ["documents", params],
    queryFn: () => api.listDocuments(params),
    // Keep polling while anything in view is still being processed.
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => d.processing && ACTIVE.has(d.processing.status))
        ? POLL_MS
        : false,
  });
}

/** Jobs across all documents; polls while any job is queued or running. */
export function useJobs(params: JobListParams) {
  return useQuery({
    queryKey: ["jobs", params],
    queryFn: () => api.listJobs(params),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const counts = query.state.data?.counts;
      return counts && (counts.queued || counts.running) ? POLL_MS : false;
    },
  });
}

/** Every job of one document, newest first; polls while the latest is active. */
export function useProcessingHistory(id: string) {
  return useQuery({
    queryKey: ["processing-history", id],
    queryFn: () => api.getProcessingHistory(id),
    refetchInterval: (query) => {
      const latest = query.state.data?.[0];
      return latest && ACTIVE.has(latest.status) ? POLL_MS : false;
    },
  });
}

export function useSearch(q: string, mode: SearchMode) {
  return useQuery({
    queryKey: ["search", q, mode],
    queryFn: () => api.search(q, mode),
    enabled: q.length > 0,
    // Keep showing the last results while a new query loads.
    placeholderData: keepPreviousData,
    // A config error (e.g. semantic search disabled) won't fix itself.
    retry: false,
  });
}

export function useDocumentStats() {
  return useQuery({
    // Under ["documents"], so uploads and deletes refresh it too.
    queryKey: ["documents", "stats"],
    queryFn: api.getStats,
    refetchInterval: (query) => (query.state.data?.in_progress ? POLL_MS : false),
  });
}

/** Upload limits; they only change when the backend is reconfigured. */
export function useUploadLimits() {
  return useQuery({
    queryKey: ["upload-limits"],
    queryFn: api.getUploadLimits,
    staleTime: Infinity,
  });
}

export function useUpload() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => api.uploadDocuments(files),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["documents"] });
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDeleteDocuments() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => api.deleteDocuments(ids),
    onSuccess: (result) => {
      // Drop cached detail data so nothing refetches a deleted document.
      for (const id of result.deleted) {
        for (const key of [
          "document",
          "processing",
          "processing-history",
          "invoice",
          "pages",
          "sheets",
          "sheet",
          "corrections",
        ]) {
          client.removeQueries({ queryKey: [key, id] });
        }
      }
      void client.invalidateQueries({ queryKey: ["documents"] });
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDocument(id: string) {
  return useQuery({ queryKey: ["document", id], queryFn: () => api.getDocument(id) });
}

export function useProcessing(id: string) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ["processing", id],
    queryFn: async () => {
      const job = await notFoundIsEmpty(() => api.getProcessing(id))();
      // When a job finishes, refresh everything derived from it.
      if (job && !ACTIVE.has(job.status)) {
        void client.invalidateQueries({ queryKey: ["invoice", id] });
        void client.invalidateQueries({ queryKey: ["pages", id] });
        void client.invalidateQueries({ queryKey: ["sheets", id] });
        void client.invalidateQueries({ queryKey: ["document", id] });
        void client.invalidateQueries({ queryKey: ["processing-history", id] });
      }
      return job;
    },
    refetchInterval: (query) =>
      query.state.data && ACTIVE.has(query.state.data.status) ? POLL_MS : false,
  });
}

/** Retry a document's failed processing; `mutate(documentId)`. */
export function useRetry() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.retryProcessing(id),
    onSuccess: (_job, id) => {
      for (const key of ["processing", "processing-history", "document"]) {
        void client.invalidateQueries({ queryKey: [key, id] });
      }
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useInvoice(id: string) {
  return useQuery({
    queryKey: ["invoice", id],
    queryFn: notFoundIsEmpty(() => api.getInvoice(id)),
  });
}

export function useCorrectInvoice(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (invoice: InvoiceData) => api.correctInvoice(id, invoice),
    onSuccess: (result) => {
      client.setQueryData(["invoice", id], result);
      void client.invalidateQueries({ queryKey: ["corrections", id] });
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useSetErrorResolved(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ errorId, resolved }: { errorId: string; resolved: boolean }) =>
      api.setErrorResolved(id, errorId, resolved),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["invoice", id] });
      void client.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useCorrections(id: string) {
  return useQuery({ queryKey: ["corrections", id], queryFn: () => api.getCorrections(id) });
}

export function usePages(id: string) {
  return useQuery({ queryKey: ["pages", id], queryFn: () => api.getPages(id) });
}

export function useSheets(id: string) {
  return useQuery({ queryKey: ["sheets", id], queryFn: () => api.getSheets(id) });
}

export function useSheet(id: string, sheet: number | null) {
  return useQuery({
    queryKey: ["sheet", id, sheet],
    queryFn: () => api.getSheet(id, sheet ?? 1),
    enabled: sheet !== null,
  });
}
