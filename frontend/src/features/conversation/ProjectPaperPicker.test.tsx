import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectPaperPicker } from "./ProjectPaperPicker";

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json"}});
function setup(options: {empty?: boolean; conflict?: boolean; failure?: boolean} = {}) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: Infinity}, mutations: {retry: false}}});
  client.setQueryData(["me"], {user_id: "picker-test"});
  let revision = 1;
  let selected: string[] = [];
  let conflict = options.conflict;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/v1/me") return response({user_id: "picker-test"});
    if (url.includes("/library/upload-jobs")) return response({id: "upload-1", status: "queued"});
    if (url === "/api/v1/jobs/upload-1") return response({id: "upload-1", status: "succeeded", result: {paper_id: "P-upload"}});
    if (url === "/api/v1/jobs/analysis-1") return response({id: "analysis-1", status: "succeeded", job_type: "planning.matrix", progress_total: 0});
    if (url.endsWith("/discovery/confirm")) return response({matrix_analysis_job: {id: "analysis-1", status: "queued", job_type: "planning.matrix", progress_total: 0}});
    if (init?.method === "PATCH") {
      if (conflict) {conflict = false; revision = 2; return response({detail: {message: "版本已变化", code: "WORKFLOW_CONFLICT"}}, 409);}
      const body = JSON.parse(init.body as string);
      selected = [...new Set([...selected.filter(id => !body.remove_ids.includes(id)), ...body.add_ids])]; revision++;
    }
    if (options.failure) return response({detail: "Cannot read registered artifact"}, 500);
    const params = new URL(url, "http://localhost").searchParams;
    const query = params.get("query"); const offset = Number(params.get("offset") || 0);
    const items = options.empty ? [] : [{paper_id: offset ? "P051" : query ? "P002" : "P001", title: offset ? "第51篇论文" : query ? "另一篇论文" : "已有论文", parsed: true, selected: false}];
    return response({revision, selected_paper_ids: selected, selected_items: selected.map(paper_id => ({paper_id, title: `已纳入论文 ${paper_id}`, available: true})), confirmed_paper_ids: [], items, total: options.empty ? 0 : query ? 1 : 51, next_offset: !options.empty && !query && !offset ? 50 : null, selection_pending: !!selected.length});
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(<QueryClientProvider client={client}><MemoryRouter><ProjectPaperPicker projectId="p1" /></MemoryRouter></QueryClientProvider>);
  return {...view, fetch, client};
}
beforeEach(() => {
  vi.spyOn(HTMLDialogElement.prototype, "showModal").mockImplementation(function (this: HTMLDialogElement) {this.setAttribute("open", "");});
  vi.spyOn(HTMLDialogElement.prototype, "close").mockImplementation(function (this: HTMLDialogElement) {this.removeAttribute("open");});
});
afterEach(() => {cleanup(); localStorage.clear(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals();});
async function open() {fireEvent.click(screen.getByRole("button", {name: "选择或上传项目文献"})); await screen.findByRole("checkbox");}
describe("project paper selection", () => {
  it("keeps saved paper titles in the unified paper dialog after selection refresh", async () => {
    setup(); await open(); fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", {name: "保存文献选择"}));
    await waitFor(() => expect(screen.getByText("已选择 1 篇 · 已用于分析 0 篇")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", {name: "关闭"}));
    await open();
    fireEvent.click(screen.getByText("已纳入的文献", {exact: false}));
    expect(screen.getByText("已纳入论文 P001")).toBeVisible();
    expect(screen.getByText("已选择 1 篇 · 已用于分析 0 篇")).toBeInTheDocument();
  });
  it("paginates the owned library and saves choices without triggering analysis or a model", async () => {
    const {fetch} = setup(); await open();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", {name: "下一页"}));
    await screen.findByText("第51篇论文"); fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", {name: "保存文献选择"}));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
    const [url, init] = fetch.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(url).toBe("/api/v1/projects/p1/discovery/papers");
    expect(JSON.parse(init!.body as string)).toEqual({revision: 1, add_ids: ["P001", "P051"], remove_ids: []});
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
  it("retains unsaved choices through search, close, and leaving the page", async () => {
    const first = setup(); await open(); fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByRole("textbox"), {target: {value: "另一篇"}});
    fireEvent.click(screen.getByRole("button", {name: "搜索"})); await screen.findByText("另一篇论文");
    fireEvent.click(screen.getByRole("checkbox")); fireEvent.click(screen.getByRole("button", {name: "关闭"}));
    fireEvent.click(screen.getByRole("button", {name: "选择或上传项目文献"}));
    expect(screen.getByRole("checkbox")).toBeChecked();
    first.unmount(); setup(); await open();
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByText("已选择 2 篇 · 已用于分析 0 篇")).toBeInTheDocument();
  });
  it("does not overwrite a revision conflict and keeps edits on explicit reload", async () => {
    const {fetch} = setup({conflict: true}); await open(); fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", {name: "保存文献选择"}));
    await screen.findByRole("button", {name: "读取最新名单，保留勾选"});
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", {name: "读取最新名单，保留勾选"}));
    await waitFor(() => expect(screen.queryByRole("button", {name: "读取最新名单，保留勾选"})).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", {name: "保存文献选择"}));
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(2));
    expect(JSON.parse(fetch.mock.calls.filter(([, init]) => init?.method === "PATCH")[1][1]!.body as string).revision).toBe(2);
  });
  it("checks a completed PDF upload but requires an explicit save and analysis confirmation", async () => {
    const {fetch} = setup(); await open();
    fireEvent.change(screen.getByLabelText("上传项目文献 PDF"), {target: {files: [new File(["pdf"], "paper.pdf", {type: "application/pdf"})]}});
    await screen.findByText("已入库，已勾选待保存", {exact: false});
    expect(fetch.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
    expect(screen.getByRole("button", {name: "确认并开始分析"})).toBeDisabled();
    fireEvent.click(screen.getByRole("button", {name: "保存文献选择"}));
    await waitFor(() => expect(screen.getByRole("button", {name: "确认并开始分析"})).toBeEnabled());
    fireEvent.click(screen.getByRole("button", {name: "确认并开始分析"}));
    await screen.findByText("后台任务 · planning.matrix");
    await waitFor(() => expect(screen.getByRole("button", {name: "确认并开始分析"})).toBeEnabled());
    expect(fetch.mock.calls.filter(([url]) => url.endsWith("/discovery/confirm"))).toHaveLength(1);
  });
  it("distinguishes an empty library from a failed read", async () => {
    const first = setup({empty: true}); fireEvent.click(screen.getByRole("button", {name: "选择或上传项目文献"}));
    await screen.findByText("文献库暂无论文，可以先上传 PDF。"); first.unmount();
    setup({failure: true}); fireEvent.click(screen.getByRole("button", {name: "选择或上传项目文献"}));
    await screen.findByRole("button", {name: "读取最新名单，保留勾选"});
    expect(screen.queryByText("文献库暂无论文，可以先上传 PDF。")).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name: "保存文献选择"})).toBeDisabled();
  });
});
