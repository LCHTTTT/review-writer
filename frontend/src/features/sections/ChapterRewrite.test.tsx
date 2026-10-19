import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {apiRequest} from "../../api/client";
import {usePreferences} from "../../state/preferences";
import {ChapterRewrite} from "./ChapterRewrite";
vi.mock("../../api/client", async original => ({...await original<object>(), apiRequest: vi.fn()}));
let currentJob: unknown = null;
vi.mock("../../hooks/useJob", () => ({useJob: () => ({data: currentJob, error: null}), jobIsActive: (value: string) => ["queued", "running"].includes(value)}));
afterEach(() => {cleanup(); vi.resetAllMocks(); localStorage.clear(); currentJob = null;});
function setup(index = "base") {
  usePreferences.getState().setLanguage("en");
  vi.mocked(apiRequest).mockImplementation(async path => path === "/api/v1/me" ? {user_id: "owner"} : {id: "task", status: "queued"} as never);
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}}), refresh = vi.fn(async () => {});
  const view = (id = index) => <QueryClientProvider client={client}><ChapterRewrite projectId="project" sectionId="S1" indexArtifactId={id} refresh={refresh} /></QueryClientProvider>;
  return {...render(view()), view, refresh};
}
it("reading does not generate and the explicit request targets only this upstream chapter", async () => {
  setup();
  fireEvent.click(await screen.findByText("Revise this upstream chapter"));
  expect(vi.mocked(apiRequest).mock.calls.every(([, init]) => !init?.method)).toBe(true);
  fireEvent.change(screen.getByRole("textbox", {name: "Chapter revision request"}), {target: {value: "Improve this whole chapter"}});
  fireEvent.click(screen.getByRole("button", {name: "Generate chapter revision candidate"}));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/v1/projects/project/sections/S1/rewrite-jobs", expect.objectContaining({method: "POST"})));
  const [, init] = vi.mocked(apiRequest).mock.calls.find(([path]) => path.includes("rewrite-jobs"))!;
  expect(JSON.parse(String(init?.body))).toEqual({message: "Improve this whole chapter", expected_sections_artifact_id: "base"});
  expect(init?.headers).toHaveProperty("Idempotency-Key");
});
it("a generated candidate is never automatically adopted and stale versions cannot overwrite upstream", async () => {
  currentJob = {id: "task", status: "succeeded", result: {candidate_artifact_id: "candidate", base_sections_artifact_id: "base", original_section: {draft_md: "Old chapter"}, section: {draft_md: "New chapter"}}};
  const {rerender, view} = setup();
  fireEvent.click(await screen.findByText("Revise this upstream chapter"));
  expect(screen.getByRole("button", {name: "Adopt into upstream chapter"})).toBeEnabled();
  expect(vi.mocked(apiRequest).mock.calls.every(([, init]) => !init?.method)).toBe(true);
  rerender(view("new-base"));
  expect(screen.getByRole("button", {name: "Adopt into upstream chapter"})).toBeDisabled();
});
it("explicit adoption writes upstream only and explains that Draft remains unchanged", async () => {
  currentJob = {id: "task", status: "succeeded", result: {candidate_artifact_id: "candidate", base_sections_artifact_id: "base", original_section: {draft_md: "Old"}, section: {draft_md: "New"}}};
  const {refresh} = setup();
  fireEvent.click(await screen.findByText("Revise this upstream chapter"));
  fireEvent.click(screen.getByRole("button", {name: "Adopt into upstream chapter"}));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(apiRequest).toHaveBeenCalledWith("/api/v1/projects/project/sections/rewrite-candidates/candidate/accept", {method: "POST"});
  expect(screen.getByRole("status")).toHaveTextContent("Draft prose is unchanged");
});
