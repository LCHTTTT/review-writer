import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiRequest } from "../../api/client";
import { usePreferences } from "../../state/preferences";
import { DraftPage } from "./DraftPage";

vi.mock("../../api/client", async original => ({ ...await original<object>(), apiRequest: vi.fn() }));
vi.mock("../../components/ProjectSelector", () => ({ ProjectSelector: () => <span>Project selector</span>,
  useSelectedProject: () => ({ selected: { project_id: "p" } }) }));
const paragraph = { paragraph_key: "k", paragraph_id: "S1-p1", text: "Saved paragraph", text_sha256: "a".repeat(64) };
const draft = { revision: 1, draft_artifact_id: "draft1", paragraphs: [paragraph], first_draft_md: "Full manuscript text",
  sections: [{ section_id: "S1", title: "Chapter one", paragraphs: [paragraph] }], section_task_states: {},
  paragraph_task_states: {}, rewrite_candidates: [], quality: {}, freshness: { upstream_stale: false }, versions: [] };
beforeEach(() => {
  usePreferences.getState().setLanguage("en");
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return draft;
    if (path.includes("section-dialogues")) return { turns: [] };
    throw new Error("Unexpected request: " + path);
  });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); localStorage.clear(); });
function RouteProbe() { const location = useLocation(); const navigate = useNavigate();
  return <><output data-testid="route">{location.pathname}{location.search}</output><button onClick={() => navigate(-1)}>Browser back</button></>;
}
function mount(url = "/draft?project=p&tab=preview") {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>
    <MemoryRouter initialEntries={[url]}><DraftPage /><RouteProbe /></MemoryRouter>
  </QueryClientProvider>);
}
it("opens chapter dialogue alongside the manuscript by default without generating content", async () => {
  mount("/draft?project=p");
  expect(await screen.findByRole("button", { name: "Chapter versions" })).toBeVisible();
  expect(screen.getByText("Full manuscript text")).toBeVisible();
  const discuss = screen.getByRole("button", { name: "Discuss current chapter" });
  expect(discuss).toHaveAttribute("aria-expanded", "true");
  expect(discuss.closest(".draft-reader-toolbar")).not.toBeNull();
  expect(vi.mocked(apiRequest).mock.calls.every(([, options]) => !options?.method)).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Back to manuscript" }));
  expect(screen.queryByRole("button", { name: "Chapter versions" })).toBeNull();
  expect(screen.getByRole("button", { name: "Discuss current chapter" })).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(screen.getByRole("button", { name: "Discuss current chapter" }));
  expect(await screen.findByRole("button", { name: "Chapter versions" })).toBeVisible();
});
it("refreshes overview source readiness after reassembling upstream content", async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  let assembled = false;
  vi.mocked(apiRequest).mockImplementation(async (path, options) => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/assemble") && options?.method === "POST") { assembled = true; return { revision: 2 }; }
    if (path.endsWith("/draft")) return { ...draft, revision: assembled ? 2 : 1,
      freshness: { upstream_stale: !assembled } };
    if (path.endsWith("/overview")) return { revision: assembled ? 2 : 1, draft_available: true,
      draft_source_stale: !assembled, history: [] };
    if (path.includes("section-dialogues")) return { turns: [] };
    throw new Error("Unexpected request: " + path);
  });
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Overview" }));
  await waitFor(() => expect(screen.getByText(/Manuscript or figure sources changed/)).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Generate new version" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "Reassemble from upstream" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Reassemble from upstream" })).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Overview" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Generate new version" })).toBeEnabled());
  expect(screen.queryByText(/Manuscript or figure sources changed/)).not.toBeInTheDocument();
  expect(apiRequest).not.toHaveBeenCalledWith(expect.stringContaining("/overview-jobs"), expect.anything());
});
it("places editable keywords after the abstract and before the overview without duplication", async () => {
  const abstract = { ...paragraph, paragraph_id: "SABS-p1", paragraph_key: "abstract", text: "Abstract text" };
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, paragraphs: [abstract, paragraph],
      manuscript_fields: { title: "Review title", keywords: ["Catalysis"] },
      manuscript_preview_md: "# Review title\n\n## Abstract\n\nAbstract text\n\n<!-- paragraph_id: SABS-p1 -->\n\n**Keywords:** Catalysis\n\n![Overview figure](/api/v1/artifacts/overview/content)\n\n## Introduction\n\nSaved paragraph\n\n<!-- paragraph_id: S1-p1 -->" };
    if (path.endsWith("/overview")) return { revision: 1, history: [] };
    return {};
  });
  mount();
  const keywords = await screen.findByRole("textbox", { name: "Keywords (comma-separated, optional)" });
  const abstractText = screen.getByRole("textbox", { name: "Paragraph text · SABS-p1" });
  const title = screen.getByRole("textbox", { name: "Title" });
  const overview = screen.getByRole("img", { name: "Overview figure" });
  expect(title.compareDocumentPosition(abstractText) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(abstractText.compareDocumentPosition(keywords) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(keywords.compareDocumentPosition(overview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByText("Catalysis")).toHaveLength(1);
  keywords.querySelector("p")!.textContent = "Catalysis, Synthesis";
  fireEvent.input(keywords);
  expect(keywords.closest(".draft-editable-manuscript")).toContainElement(screen.getByRole("button", { name: "Save changes" }));
});
it("edits the marked manuscript in place, saves and refreshes the official draft", async () => {
  let current = { ...draft, first_draft_md: "## Chapter one\n\nSaved paragraph\n\n<!-- paragraph_id: S1-p1 -->" };
  vi.mocked(apiRequest).mockImplementation(async (path, init) => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return current;
    if (path.endsWith("/overview")) return { revision: 1, history: [] };
    if (path.endsWith("/paragraphs/S1-p1") && init?.method === "PUT") {
      const value = JSON.parse(init.body as string).text;
      current = { ...current, revision: 2, first_draft_md: `## Chapter one\n\n${value}\n\n<!-- paragraph_id: S1-p1 -->`, paragraphs: [{ ...paragraph, text: value, text_sha256: "b".repeat(64) }] };
      return { revision: 2 };
    }
    throw new Error("Unexpected " + path);
  });
  mount();
  const input = await screen.findByRole("textbox", { name: "Paragraph text · S1-p1" });
  input.querySelector("p")!.textContent = "Author edited paragraph";
  fireEvent.input(input);
  expect(screen.getByRole("textbox")).toBe(input);
  fireEvent.click(screen.getByRole("button", { name: "Save paragraph" }));
  await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
  expect(screen.getByRole("textbox")).toHaveTextContent("Author edited paragraph");
  expect(localStorage.getItem("rw-draft-scratch:u:p:k:manual")).toBeNull();
});
it("renders the composed overview manuscript instead of raw editable prose", async () => {
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, manuscript_preview_md: "# Title\n\n![Overview figure](/api/v1/artifacts/overview/content)\n\n*Saved overview caption.*\n\n## Introduction\n\nFull manuscript text" };
    if (path.endsWith("/overview")) return { revision: 1, overview_figure_exists: false };
    throw new Error("Unexpected request: " + path);
  });
  mount("/draft?project=p&tab=preview");
  expect(await screen.findByRole("img", { name: "Overview figure" })).toHaveAttribute("src", "/api/v1/artifacts/overview/content");
  expect(screen.getByText(/Saved overview caption\./)).toBeTruthy();
});
it("starts with the manuscript and opens optional tools without a numbered wizard", async () => {
  mount();
  const nav = await screen.findByRole("navigation", { name: "Draft workspace" });
  expect(within(nav).getAllByRole("button")).toHaveLength(2);
  expect(screen.getByText("Full manuscript text")).toBeVisible();
  const readerToolbar = screen.getByRole("button", { name: "Hide contents" }).closest(".draft-reader-toolbar")!;
  expect(within(readerToolbar as HTMLElement).getByRole("group", { name: "Complete manuscript" })).toBeVisible();
  expect(document.querySelector(".draft-document-tools")).toBeNull();
  expect(screen.queryByRole("button", { name: "Review and revise manuscript" })).toBeNull();
  fireEvent.click(within(nav).getByRole("button", { name: "Review-style revision" }));
  expect(screen.getByRole("button", { name: "Review and revise manuscript" })).toBeEnabled();
  fireEvent.click(within(nav).getByRole("button", { name: "Manuscript" }));
  fireEvent.click(screen.getByRole("button", { name: "Discuss current chapter" }));
  expect(await screen.findByRole("button", { name: "Chapter versions" })).toBeTruthy();
  expect(screen.getByText("Full manuscript text")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Back to manuscript" }));
  expect(screen.queryByRole("button", { name: "Chapter versions" })).toBeNull();
  expect(screen.getByRole("button", { name: "Approve and enter Final" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Review approval details" }));
  expect(screen.getByRole("heading", { name: "Waiting for human approval" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "History" }));
  expect(screen.getByText("No historical versions yet.")).toBeTruthy();
});
it("removes auxiliary wording clues from peer review without launching tasks or blocking approval", async () => {
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, narrative_repetition: {
      status: "complete", method: "surface_clues_v1", covered_paragraph_count: 1,
      total_paragraph_count: 1, hints: [{ kind: "repeated_closing", paragraph_ids: ["S1-p1"],
        phrase: "taken together", basis: "surface_wording_only" }],
    } };
    if (path.includes("section-dialogues")) return { turns: [] };
    throw new Error("Unexpected request: " + path);
  });
  mount();
  const nav = await screen.findByRole("navigation", { name: "Draft workspace" });
  fireEvent.click(within(nav).getByRole("button", { name: "Review-style revision" }));
  expect(screen.queryByText("Manuscript writing notes")).not.toBeInTheDocument();
  expect(screen.queryByText(/1 possible wording repetitions/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve and enter Final" })).toBeEnabled();
  expect(vi.mocked(apiRequest).mock.calls.every(([, options]) => !options || options.method !== "POST")).toBe(true);
});
it("keeps Final approval in the bottom action bar and blocks stale manuscripts", async () => {
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, freshness: { upstream_stale: true } };
    if (path.endsWith("/overview")) return { revision: 1, history: [] };
    throw new Error("Unexpected request: " + path);
  });
  mount();
  const action = await screen.findByRole("button", { name: "Approve and enter Final" });
  expect(action.closest(".stage-action-bar")).not.toBeNull();
  expect(action).toBeDisabled();
});
it("confirms the saved manuscript from the bottom bar and enters Final once", async () => {
  let current = { ...draft, draft_approval_current: false };
  const approvalPosts: string[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, init) => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return current;
    if (path.endsWith("/overview")) return { revision: 1, history: [] };
    if (path.endsWith("/draft/approve") && init?.method === "POST") {
      approvalPosts.push(path);
      current = { ...current, draft_approval_current: true };
      return { status: "approved" };
    }
    throw new Error("Unexpected request: " + path);
  });
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Approve and enter Final" }));
  await waitFor(() => expect(screen.getByTestId("route")).toHaveTextContent("/final?project=p"));
  expect(approvalPosts).toHaveLength(1);
});
it("groups batch actions and suggestions without duplicating the full manuscript", async () => {
  const candidate = { candidate_id: "c1", paragraph_key: "k", paragraph_id: "S1-p1", original_text: "Saved paragraph",
    candidate_text: "Improved paragraph", reply: "Clearer comparison", status: "pending", revision_mode: "dialogue" };
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, rewrite_candidates: [candidate], dialogue_batch_job: {
      id: "batch1", status: "succeeded", progress_current: 1, progress_total: 1 } };
    throw new Error("Unexpected request: " + path);
  });
  mount("/draft?project=p&tab=batch");
  expect(await screen.findByRole("heading", { name: "Review-style revision", level: 2 })).toBeVisible();
  expect(screen.getByRole("button", { name: "Review and revise manuscript" })).toBeEnabled();
  expect(screen.getByRole("region", { name: "Revision suggestions" })).toBeVisible();
  expect(screen.queryByText("Preview combined paragraphs (not saved)")).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select" }));
  expect(screen.getByRole("button", { name: "Save selected (1)" })).toBeEnabled();
});
it("preserves old reports as read-only without restoring per-finding paid actions", async () => {
  vi.mocked(apiRequest).mockImplementation(async path => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, peer_review_job: { id: "review-edit", status: "succeeded",
      source_draft_artifact_id: "draft1", result: { issues: [{ issue_id: "i", paragraph_id: "S1-p1", excerpt: "Saved paragraph",
        diagnosis: "Needs clarification", instruction: "Qualify this paragraph", route: "manual", status: "open", basis: "suggestion" }] } } };
    if (path.includes("section-dialogues")) return { turns: [] };
    throw new Error("Unexpected request: " + path);
  });
  mount("/draft?project=p&tab=batch");
  expect(await screen.findByText('Historical review (read-only)')).toBeVisible();
  fireEvent.click(screen.getByText('Read original report'));
  expect(screen.getByText('Needs clarification')).toBeVisible();
  expect(screen.queryByRole('button', {name:'Repair this finding'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name:'Check this finding'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name:'Edit paragraph'})).not.toBeInTheDocument();
  expect(screen.getByTestId('route')).toHaveTextContent('tab=batch');
  expect(vi.mocked(apiRequest).mock.calls.every(([, options]) => !options?.method)).toBe(true);
});
it("resumes an interrupted peer review without submitting the old batch route", async () => {
  const requests: string[] = [];
  vi.mocked(apiRequest).mockImplementation(async (path, init) => {
    if (path === "/api/v1/me") return { user_id: "u" };
    if (path.endsWith("/draft")) return { ...draft, peer_review_job: {
      id: "review1", status: "failed", progress_current: 0, progress_total: 1,
      source_draft_artifact_id: "draft1", result: { policy: "review-revision/2", phase: "audit" },
    } };
    if (init?.method === "POST") { requests.push(path); return { id: "batch2" }; }
    throw new Error("Unexpected request: " + path);
  });
  mount("/draft?project=p&tab=batch");
  expect(await screen.findByText(/Revision did not finish/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Resume unfinished revision" }));
  await waitFor(() => expect(requests).toEqual(["/api/v1/jobs/review1/retry"]));
});
it("restores the selected column from the URL and preserves paragraph deep links", async () => {
  const view = mount("/draft?project=p&tab=history");
  await screen.findByText("No historical versions yet.");
  view.unmount();
  mount("/draft?project=p&paragraph=S1-p1");
  fireEvent.click(await screen.findByRole("button", { name: "Chapter versions" }));
  expect(await screen.findByText("Saved paragraph")).toBeVisible();
  expect(screen.queryByLabelText("Revision scope")).not.toBeInTheDocument();
});

it("expands and restores dialogue without losing the input or remounting the reader", async () => {
  mount("/draft?project=p&paragraph=S1-p1");
  const expand = await screen.findByRole("button", { name: "Expand dialogue" });
  const input = screen.getByRole("textbox", { name: "Tell the AI how to improve this chapter" });
  fireEvent.change(input, { target: { value: "Compare the key results" } });
  const manuscript = screen.getByText("Full manuscript text");
  fireEvent.click(expand);
  expect(manuscript).not.toBeVisible();
  expect(input).toHaveValue("Compare the key results");
  fireEvent.click(screen.getByRole("button", { name: "Restore split view" }));
  expect(screen.getByText("Full manuscript text")).toBe(manuscript);
  expect(manuscript).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Tell the AI how to improve this chapter" })).toBe(input);
  expect(input).toHaveValue("Compare the key results");
});
it("late batch submission does not pull the user away from another column", async () => {
  const original = vi.mocked(apiRequest).getMockImplementation()!;
  let finish!: (value: unknown) => void;
  vi.mocked(apiRequest).mockImplementation((path, init) => path.endsWith("/review-jobs")
    ? new Promise(resolve => { finish = resolve; }) : original(path, init));
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Review-style revision" }));
  fireEvent.click(await screen.findByRole("button", { name: "Review and revise manuscript" }));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "Manuscript" }));
  await act(async () => { finish({ id: "batch" }); });
  expect(screen.getByText("Full manuscript text")).toBeTruthy();
  expect(screen.getByTestId("route")).toHaveTextContent("tab=preview");
});
