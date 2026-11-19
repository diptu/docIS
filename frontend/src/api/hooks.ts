import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, api, type ListParams } from "./client";
import type { InvoiceData } from "./types";

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

export function useUpload() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) => api.uploadDocuments(files),
    onSuccess: () => client.invalidateQueries({ queryKey: ["documents"] }),
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
      }
      return job;
    },
    refetchInterval: (query) =>
      query.state.data && ACTIVE.has(query.state.data.status) ? POLL_MS : false,
  });
}

export function useRetry(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.retryProcessing(id),
    onSuccess: () => client.invalidateQueries({ queryKey: ["processing", id] }),
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
