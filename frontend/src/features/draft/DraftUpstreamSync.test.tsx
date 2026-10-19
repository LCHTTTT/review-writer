import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {apiRequest} from "../../api/client";
import {usePreferences} from "../../state/preferences";
import {DraftUpstreamSync} from "./DraftUpstreamSync";
vi.mock("../../api/client", async original => ({...await original<object>(), apiRequest: vi.fn()}));
afterEach(() => {cleanup(); vi.resetAllMocks();});
const value = {section_id: "S1", draft_artifact_id: "draft", sections_artifact_id: "upstream", changes: [{paragraph_id: "S1-p1", original_text: "Saved manual text", candidate_text: "Merged text"}], conflicts: []};
function setup(requireSaved = vi.fn(), sections = [{section_id: "S1", title: "Chapter one"}]) {
  usePreferences.getState().setLanguage("en");
  const client = new QueryClient({defaultOptions: {mutations: {retry: false}}}), refresh = vi.fn(async () => {});
  const view = (id = "draft") => <QueryClientProvider client={client}><DraftUpstreamSync projectId="project" draftArtifactId={id} sections={sections} requireSaved={requireSaved} refresh={refresh} /></QueryClientProvider>;
  return {...render(view()), view, refresh};
}
it("mounting and previewing do not write; explicit save uses both shown versions", async () => {
  vi.mocked(apiRequest).mockResolvedValueOnce(value).mockResolvedValueOnce({});
  const {refresh} = setup();
  expect(apiRequest).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Review upstream chapter changes"));
  fireEvent.click(screen.getByRole("button", {name: "Preview differences"}));
  await screen.findByText("S1-p1 · Prose changes");
  expect(apiRequest).toHaveBeenCalledOnce();
  expect(vi.mocked(apiRequest).mock.calls[0][1]).toBeUndefined();
  fireEvent.click(screen.getByRole("button", {name: "Save this chapter's merged prose"}));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(JSON.parse(String(vi.mocked(apiRequest).mock.calls[1][1]?.body))).toEqual({expected_draft_artifact_id: "draft", expected_sections_artifact_id: "upstream"});
  expect(await screen.findByRole("status")).toHaveTextContent("Scientific sources");
});
it("stale preview and overlapping manual edits cannot be saved", async () => {
  vi.mocked(apiRequest).mockResolvedValue({...value, conflicts: [{paragraph_id: "S1-p1", conflicts: [{base_text: "Base", upstream_text: "Upstream", draft_edits: [{text: "Manual"}]}]}]});
  const {rerender, view} = setup();
  fireEvent.click(screen.getByText("Review upstream chapter changes"));
  fireEvent.click(screen.getByRole("button", {name: "Preview differences"}));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", {name: "Save this chapter's merged prose"})).toBeDisabled();
  rerender(view("new-draft"));
  expect(screen.getByRole("status")).toHaveTextContent("The Draft changed");
  expect(apiRequest).toHaveBeenCalledOnce();
});
it("unsaved input blocks writing without dropping the preview", async () => {
  vi.mocked(apiRequest).mockResolvedValue(value);
  setup(vi.fn(() => {throw new Error("Save unsaved edits first");}));
  fireEvent.click(screen.getByText("Review upstream chapter changes"));
  fireEvent.click(screen.getByRole("button", {name: "Preview differences"}));
  await screen.findByText("S1-p1 · Prose changes");
  fireEvent.click(screen.getByRole("button", {name: "Save this chapter's merged prose"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("Save unsaved edits first");
  expect(apiRequest).toHaveBeenCalledOnce();
  expect(screen.getByText("S1-p1 · Prose changes")).toBeInTheDocument();
});
it("an empty Draft is an ordinary empty state and makes no request", () => {
  setup(vi.fn(), []);
  fireEvent.click(screen.getByText("Review upstream chapter changes"));
  expect(screen.getByRole("status")).toHaveTextContent("No chapters");
  expect(apiRequest).not.toHaveBeenCalled();
});
