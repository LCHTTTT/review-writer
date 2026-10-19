import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useSectionStatusSync } from "./useSectionStatusSync";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("refreshes artifacts when externally started work completes, but not for unchanged status", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const reset = vi.fn();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ artifact_ids: {}, job_id: "old", job_status: "failed" }));
  const invalidate = vi.spyOn(client, "invalidateQueries");
  function Harness() { useSectionStatusSync("p", true, reset); return null; }
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  await waitFor(() => expect(reset).toHaveBeenCalledOnce());
  invalidate.mockClear(); reset.mockClear();
  const complete = { artifact_ids: { index: "published" }, job_id: "external", job_status: "succeeded" };
  await act(async () => { client.setQueryData(["section-status", "p"], complete); });
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sections", "p"] }));
  expect(reset).toHaveBeenCalledOnce();
  invalidate.mockClear(); reset.mockClear();
  await act(async () => { client.setQueryData(["section-status", "p"], { ...complete }); });
  expect(invalidate).not.toHaveBeenCalled();
  expect(reset).not.toHaveBeenCalled();
  client.clear();
});

it("does not poll before a plan is confirmed", async () => {
  const client = new QueryClient();
  const fetch = vi.spyOn(globalThis, "fetch");
  function Harness() { useSectionStatusSync("p", false, () => {}); return null; }
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  expect(fetch).not.toHaveBeenCalled();
  client.clear();
});
