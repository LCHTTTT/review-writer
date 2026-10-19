import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationPage } from "./ConversationPage";
import { isStagePath, rememberStage, stageReturnPath } from "./modeNavigation";
import { usePreferences } from "../../state/preferences";

function setup(path = "/conversation?project=p1", active = false, proposalStatus?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["me"], { user_id: "u1" });
  client.setQueryData(["model-catalog"], { items: [{ id: "test", label_zh: "测试模型", model: "offline" }, { id: "alternative", label_zh: "替代模型", model: "offline-alt" }] });
  client.setQueryData(["projects"], { items: [{ project_id: "p1", slug: "综述项目", topic: "Copper", completed_stages: [], current_stage: "draft" }] });
  client.setQueryData(["conversation-overview", "p1"], { topic: "Copper", model_tier: "test",
    stages: [{ id: "draft", checkpoints: [{ id: "draft", status: "pending", revision: 1 }] }],
    artifacts: [{ id: "a1", label: "初稿", stage: "draft", logical_name: "draft/manuscript.md", current: true,
      created_at: "2026-10-10T10:00:00", format: "md", url: "/api/v1/artifacts/a1/content" }], jobs: [] });
  client.setQueryData(["conversation-history", "p1"], { turns: proposalStatus ? [{ id: "turn1", status: proposalStatus, message: "保留原图", result: { reply: "待采用",
    actions: [{ id: "action1", name: "images.preserve_sources", label: "保留原图，不重绘", arguments: {}, notice: "未执行" }] } }] : active ? [{ id: "j1", status: "running", message: "已有问题", result: {} }] : [{ id: "turn0", status: "succeeded", message: "看看成果", result: { reply: "已保存初稿", artifact_ids: ["a1"] } }], next_cursor: null });
  client.setQueryData(["conversation-draft-edit", "p1", "a1"], { revision: 1, draft_artifact_id: "a1", first_draft_md: "# 已保存初稿\n\n已保存正文\n\n<!-- paragraph_id: S01-p1 -->",
    paragraphs: [{ paragraph_id: "S01-p1", paragraph_key: "stable-key", text: "已保存正文", text_sha256: "h1" }] });
  let actionJob: Record<string, unknown> | null = null;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/discovery/papers")) return new Response(JSON.stringify({revision: 0, items: [], selected_paper_ids: [], confirmed_paper_ids: [], total: 0, next_offset: null, selection_pending: false}), {headers: {"Content-Type": "application/json"}});
    if (url === "/api/v1/taxonomy-profiles") return new Response(JSON.stringify({ default_profile: "chemistry_general", items: [{ id: "chemistry_general", label_zh: "通用化学", label_en: "Chemistry" }] }), { headers: { "Content-Type": "application/json" } });
    if (url === "/api/v1/projects" && init?.method === "POST") {
      const fields = JSON.parse(init.body as string);
      const project = { project_id: "p2", ...fields, completed_stages: [], current_stage: "library" };
      client.setQueryData(["projects"], { items: [project] });
      return new Response(JSON.stringify(project), { headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/conversation/preparation/messages")) return new Response(JSON.stringify({ turns: [], next_cursor: null }), { headers: { "Content-Type": "application/json" } });
    if (url.endsWith("/model-tier") && init?.method === "PATCH") {
      const tier = JSON.parse(init.body as string).model_tier;
      client.setQueryData(["conversation-overview", "p1"], (state: Record<string, unknown>) => ({ ...state, model_tier: tier }));
      return new Response(JSON.stringify({ project_id: "p1", model_tier: tier }), { headers: { "Content-Type": "application/json" } });
    }
    if (url.includes("/actions/")) {
      if (init?.method === "POST") actionJob = { id: "actionjob", status: "succeeded", job_type: "conversation.action", result: { execution_completed: true, outcome: {} } };
      return new Response(JSON.stringify(init?.method === "POST" ? actionJob : { job: actionJob }), { headers: { "Content-Type": "application/json" } });
    }
    if (url === "/api/v1/projects") return new Response(JSON.stringify(client.getQueryData(["projects"])), { headers: { "Content-Type": "application/json" } });
    if (init?.method === "POST") return new Response(JSON.stringify({ id: "j2", status: "queued" }), { headers: { "Content-Type": "application/json" } });
    if (url.includes("/versions/")) return new Response(JSON.stringify({ items: client.getQueryData<{ artifacts: unknown[] }>(["conversation-overview", "p1"])!.artifacts }), { headers: { "Content-Type": "application/json" } });
    if (url.includes("/artifacts/a1")) {
      const language = new URL(url, "http://testserver").searchParams.get("language");
      if (!["zh", "en"].includes(language ?? "")) return new Response(JSON.stringify({ detail: "Invalid artifact language" }), { status: 422, headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify({ text: "# 已保存初稿", current: true, complete: true, next_offset: null, total_chars: 8 }), { headers: { "Content-Type": "application/json" } });
    }
    if (url.endsWith("/messages")) return new Response(JSON.stringify(client.getQueryData(["conversation-history", "p1"]) || { turns: [], next_cursor: null }), { headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify(client.getQueryData(["conversation-overview", "p1"])), { headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><ConversationPage /></MemoryRouter></QueryClientProvider>);
  return { ...view, client, fetch };
}

afterEach(() => { cleanup(); usePreferences.setState({ language: "zh-CN" }); localStorage.clear(); sessionStorage.clear(); vi.unstubAllGlobals(); });

describe("project conversation", () => {
  it("opens saved documents through the readable view without losing unsent input", async () => {
    usePreferences.setState({ language: "zh-CN" });
    const { fetch } = setup();
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "尚未发送" } });
    fireEvent.click(screen.getAllByRole("button", { name: "查看 初稿" })[0]);
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/artifacts/a1?") && new URL(url, "http://testserver").searchParams.get("language") === "zh")).toBe(true));
    await screen.findByRole("heading", { name: "已保存初稿" });
    expect(screen.getByLabelText("输入项目问题")).toHaveValue("尚未发送");
    expect(screen.getByRole("link", { name: "下载完整文件" })).toHaveAttribute("href", "/api/v1/artifacts/a1/content");
  });
  it("switches a saved viewer between exact API languages without losing composer text", async () => {
    usePreferences.setState({ language: "zh-CN" });
    const { fetch } = setup();
    const composer = screen.getByLabelText("输入项目问题");
    fireEvent.change(composer, { target: { value: "未提交的要求" } });
    fireEvent.click(screen.getAllByRole("button", { name: "查看 初稿" })[0]);
    await screen.findByRole("heading", { name: "已保存初稿" });
    act(() => usePreferences.setState({ language: "en" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url.includes("/artifacts/a1?") && new URL(url, "http://testserver").searchParams.get("language") === "en")).toBe(true));
    expect(composer).toHaveValue("未提交的要求");
    expect(screen.queryByRole("heading", { name: "Unable to load" })).not.toBeInTheDocument();
  });
  it("keeps an explicit invalid project instead of silently changing the target", () => {
    setup("/conversation?project=missing");
    expect(screen.getByText("项目不可用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发送" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "当前项目" })).toHaveValue("");
  });
  it("shows a normal empty workspace and an existing project entry", () => {
    setup("/conversation");
    expect(screen.getByText("先聊聊你的综述")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新建项目" })).toHaveAttribute("href", "/workspace");
  });
  it("creates a real project directly without a preparation model turn", async () => {
    const { fetch } = setup("/conversation");
    fireEvent.click(screen.getByRole("button", { name: "在这里新建项目" }));
    fireEvent.change(screen.getByRole("textbox", { name: "项目名称" }), { target: { value: "direct-review" } });
    fireEvent.change(screen.getByRole("textbox", { name: "研究主题" }), { target: { value: "Copper" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并进入对话" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) => url === "/api/v1/projects" && init?.method === "POST")).toBe(true));
    expect(fetch.mock.calls.some(([url, init]) => url.includes("preparation/messages") && init?.method === "POST")).toBe(false);
  });
  it("preserves unsent input after leaving", () => {
    const first = setup();
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "尚未发送的讨论" } });
    first.unmount(); setup();
    expect(screen.getByLabelText("输入项目问题")).toHaveValue("尚未发送的讨论");
  });
  it("sends with a stable persisted idempotency key and no guessed write operation", async () => {
    const { fetch } = setup();
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "看看现有成果" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const [, init] = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(init!.body as string)).toEqual({ message: "看看现有成果", object_id: "", web_search: false });
    expect(new Headers(init!.headers).get("Idempotency-Key")).toBeTruthy();
  });
  it("keeps new input while a reply is active and offers a separate stop control", () => {
    setup(undefined, true);
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "下一条要求" } });
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止回复" })).toBeEnabled();
    expect(screen.getByLabelText("输入项目问题")).toHaveValue("下一条要求");
  });
  it("queues a follow-up while an earlier reply is active without cancelling it", async () => {
    const { fetch } = setup(undefined, true);
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "后面再检查第三章" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const [path, init] = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(path).toBe("/api/v1/projects/p1/conversation/messages");
    expect(JSON.parse(init!.body as string)).toEqual({ message: "后面再检查第三章", object_id: "", web_search: false });
    expect(fetch.mock.calls.some(([url]) => url.includes("/cancel"))).toBe(false);
  });
  it("sends explicit steering separately from a follow-up", async () => {
    const { fetch } = setup(undefined, true);
    fireEvent.change(screen.getByRole("combobox", { name: "新消息处理方式" }), { target: { value: "steer" } });
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "第二章需要保留失败案例" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const [, init] = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(init!.body as string).delivery).toBe("steer");
  });
  it("saves the actual project model without executing a turn or losing unsent input", async () => {
    const { fetch } = setup();
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "保留未发送的要求" } });
    fireEvent.change(screen.getByRole("combobox", { name: "项目文本模型" }), { target: { value: "alternative" } });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "项目文本模型" })).toHaveValue("alternative"));
    expect(screen.getByLabelText("输入项目问题")).toHaveValue("保留未发送的要求");
    const writes = fetch.mock.calls.filter(([, init]) => ["POST", "PATCH"].includes(init?.method || ""));
    expect(writes).toHaveLength(1);
    expect(writes[0][0]).toBe("/api/v1/projects/p1/model-tier");
    expect(JSON.parse(writes[0][1]!.body as string)).toEqual({ model_tier: "alternative" });
  });
  it("places model and network controls at the composer and sends the explicit switch without triggering search on toggle", async () => {
    const {fetch} = setup();
    const network = screen.getByRole("switch", {name: "联网检索"});
    expect(network).not.toBeChecked();
    expect(network.closest(".conversation-composer")).toBeTruthy();
    expect(screen.getByRole("combobox", {name: "项目文本模型"}).closest(".conversation-composer")).toBeTruthy();
    const upload = screen.getByRole("button", {name: "选择或上传项目文献"});
    expect(upload.closest(".conversation-composer-tools")).toBeTruthy();
    expect(upload).toHaveAttribute("title", expect.stringContaining("已选择"));
    expect(upload.querySelector("svg")).toBeTruthy();
    expect(screen.getByRole("button", {name: "发送"}).closest(".conversation-composer-tools")).toBeTruthy();
    fireEvent.click(network);
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    fireEvent.change(screen.getByLabelText("输入项目问题"), {target: {value: "检索相关文献"}});
    fireEvent.click(screen.getByRole("button", {name: "发送"}));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const [,init] = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(init!.body as string)).toEqual({message: "检索相关文献", object_id: "", web_search: true});
    await screen.findByText("已提交，正在同步任务状态…");
  });
  it("does not execute an action while reading its history", async () => {
    const { fetch } = setup(undefined, false, "succeeded");
    expect((await screen.findByText("快捷执行（可选）")).parentElement).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("快捷执行（可选）"));
    await waitFor(() => expect(screen.getByRole("button", { name: "采用并执行这个操作" })).toBeEnabled());
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
  it("adopts the stored operation without a second model request", async () => {
    const { fetch } = setup(undefined, false, "succeeded");
    fireEvent.click(await screen.findByText("快捷执行（可选）"));
    await waitFor(() => expect(screen.getByRole("button", { name: "采用并执行这个操作" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "采用并执行这个操作" }));
    await screen.findByText("操作已完成；生成任务及成果状态见下方。");
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("/api/v1/projects/p1/conversation/actions/turn1/action1");
  });
  it("does not adopt a proposal from a failed turn", async () => {
    setup(undefined, false, "failed");
    fireEvent.click(await screen.findByText("快捷执行（可选）"));
    await waitFor(() => expect(screen.getByRole("button", { name: "采用并执行这个操作" })).toBeDisabled());
  });
  it("switches between split and expanded reading without losing the unsent conversation", async () => {
    const { container, fetch } = setup();
    fireEvent.change(screen.getByLabelText("输入项目问题"), { target: { value: "请比较第二章的证据" } });
    fireEvent.click(screen.getByRole("button", { name: /初稿/ }));
    expect(container.querySelector(".conversation-workspace")).toHaveClass("has-viewer");
    fireEvent.click(screen.getByRole("button", { name: "展开阅读" }));
    expect(container.querySelector(".conversation-workspace")).toHaveClass("viewer-wide");
    fireEvent.click(screen.getByRole("button", { name: "关闭查看区" }));
    expect(container.querySelector(".conversation-workspace")).not.toHaveClass("has-viewer", "viewer-wide");
    expect(screen.getByLabelText("输入项目问题")).toHaveValue("请比较第二章的证据");
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
  it("opens the actual saved result and retains manual edits when closing the viewer", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: /初稿/ }));
    await screen.findByRole("region", { name: "成果查看区" }).catch(() => undefined);
    await waitFor(() => expect(screen.getByText("已保存初稿")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "手动编辑初稿" }));
    const editor = screen.getByRole("textbox", { name: "段落内容 · S01-p1" });
    editor.textContent = "手工修改，尚未保存"; fireEvent.input(editor);
    expect(screen.getAllByText("有未保存修改").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "关闭查看区" }));
    fireEvent.click(screen.getByRole("button", { name: /初稿/ }));
    fireEvent.click(screen.getByRole("button", { name: "手动编辑初稿" }));
    expect(screen.getByRole("textbox", { name: "段落内容 · S01-p1" })).toHaveTextContent("手工修改，尚未保存");
  });
});

describe("shared Draft editor", () => {
  it("opens project papers from the bottom icon without losing the composer", () => {
    const { fetch } = setup();
    const input = screen.getByRole("textbox", { name: "输入项目问题" });
    fireEvent.change(input, { target: { value: "尚未发送的讨论" } });
    fireEvent.click(screen.getByRole("button", { name: "选择或上传项目文献" }));
    expect(input).toHaveValue("尚未发送的讨论");
    expect(screen.getByRole("dialog", {name:"本项目文献"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", {name:"对话选项"})).not.toBeInTheDocument();
    expect(input).toHaveValue("尚未发送的讨论");
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
  it("places project selection at the preparation header and preserves an unsubmitted review idea", () => {
    setup("/conversation");
    const input = screen.getByRole("textbox", { name: "输入综述需求" });
    fireEvent.change(input, { target: { value: "先讨论研究方向" } });
    expect(input).toHaveValue("先讨论研究方向");
    expect(screen.getByLabelText("当前项目")).toBeVisible();
    expect(screen.getByLabelText("当前项目").closest(".conversation-header-controls")).toBeTruthy();
    expect(input).toHaveValue("先讨论研究方向");
  });
  it("restores the original Draft page's paragraph scratch without a second copy", async () => {
    localStorage.setItem("rw-draft-scratch:u1:p1:stable-key:manual", JSON.stringify({ text: "原初稿页尚未保存的文字", hash: "h1", revision: 1 }));
    setup(); fireEvent.click(screen.getByRole("button", { name: /初稿/ }));
    await screen.findByText("已保存初稿"); fireEvent.click(screen.getByRole("button", { name: "手动编辑初稿" }));
    expect(screen.getByRole("textbox", { name: "段落内容 · S01-p1" })).toHaveTextContent("原初稿页尚未保存的文字");
  });
  it("does not share another user's unsaved paragraph", async () => {
    localStorage.setItem("rw-draft-scratch:u2:p1:stable-key:manual", JSON.stringify({ text: "其他用户的文字", hash: "h1", revision: 1 }));
    setup(); fireEvent.click(screen.getByRole("button", { name: /初稿/ }));
    await screen.findByText("已保存初稿"); fireEvent.click(screen.getByRole("button", { name: "手动编辑初稿" }));
    expect(screen.getByRole("textbox", { name: "段落内容 · S01-p1" })).toHaveTextContent("已保存正文");
  });
});

describe("mode navigation", () => {
  it("restores the same user's project stage and tab", () => {
    rememberStage("u1", "p1", "/planning?view=outline&project=p1");
    expect(stageReturnPath("u1", "p1")).toBe("/planning?view=outline&project=p1");
    expect(stageReturnPath("u2", "p1")).toBe("/library?project=p1");
    expect(stageReturnPath("u1", "p2")).toBe("/library?project=p2");
  });
  it("rejects foreign origins and non-stage routes", () => {
    rememberStage("u1", "p1", "https://example.com/draft?project=p1");
    rememberStage("u1", "p1", "/admin?project=p1");
    expect(stageReturnPath("u1", "p1")).toBe("/library?project=p1");
    expect(isStagePath("/conversation")).toBe(false);
  });
});
