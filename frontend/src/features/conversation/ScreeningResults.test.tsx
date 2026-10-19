import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScreeningResults } from "./ScreeningResults";
import { ConversationReply } from "./ConversationReply";
import type { Turn } from "./types";

afterEach(() => {cleanup(); localStorage.clear(); vi.unstubAllGlobals();});
function setup(data: unknown, status = 200) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json"}}));
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  render(<QueryClientProvider client={client}><MemoryRouter><ScreeningResults projectId="project-one" artifactId="saved-one" /></MemoryRouter></QueryClientProvider>);
  return fetch;
}
describe("original screening result in conversation", () => {
  it("uses the saved artifact and stage recommendation logic without adopting papers", async () => {
    const fetch = setup({results: [{keyword: "methods", local_results: [
      {paper_id: "P1", title: "Relevant method", score: 0.8, recommendation_status: "recommended"},
      {paper_id: "P2", title: "Needs review", score: 0.1, recommendation_status: "review"},
      {paper_id: "P3", title: "Excluded", score: 0.9, role: "excluded"},
    ]}, {keyword: "repeat", local_results: [{paper_id: "P1", title: "Relevant method", score: 0.8, recommendation_status: "recommended"}]}]});
    await screen.findByText("推荐 1 篇 · 待核对 1 篇 · 联网候选 0 篇");
    expect(screen.getAllByText("Relevant method")).toHaveLength(1);
    expect(screen.queryByText("Excluded")).not.toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/discovery?project=project-one");
    expect(fetch).toHaveBeenCalledWith("/api/v1/artifacts/saved-one/content", expect.objectContaining({credentials: "same-origin"}));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/不代表已经采用/)).toBeInTheDocument();
  });
  it("distinguishes online-only results from a failed artifact read", async () => {
    setup({results: [{keyword: "topic", web_results: [{candidate_id: "web-one", title: "Online paper"}]}]});
    await screen.findByText("推荐 0 篇 · 待核对 0 篇 · 联网候选 1 篇");
    expect(screen.getByText(/联网候选须获取全文并入库/)).toBeInTheDocument();
    cleanup(); setup({detail: "Unable to read saved result"}, 500);
    await screen.findByRole("alert");
    expect(screen.queryByText(/本次没有可展示的本地候选/)).not.toBeInTheDocument();
  });
});
describe("actual conversation phases", () => {
  it("resumes only an eligible failed latest reply via the existing retry endpoint", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({id: "retry-one", status: "queued"}), {status: 202, headers: {"Content-Type": "application/json"}}));
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({defaultOptions: {mutations: {retry: false}}});
    const turn = {id: "failed-one", status: "failed", job_type: "conversation.turn", available_actions: ["retry"],
      error_code: "CONVERSATION_MODEL_FAILED", error_message: "模型请求已结束，但未返回有效回复。", result: {}} as Turn;
    const view = render(<QueryClientProvider client={client}><ConversationReply turn={turn} allowResume /></QueryClientProvider>);
    expect(screen.getByRole("alert")).toHaveTextContent("未返回有效回复");
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "从中断处继续回复"}));
    await waitFor(() => expect(screen.getByRole("button", {name: "已提交继续回复"})).toBeDisabled());
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith("/api/v1/jobs/failed-one/retry", expect.objectContaining({method: "POST"}));
    view.rerender(<QueryClientProvider client={client}><ConversationReply turn={turn} /></QueryClientProvider>);
    expect(screen.queryByRole("button", {name: /继续回复/})).not.toBeInTheDocument();
  });
  it("separates queued work from model wait and keeps a failure visible", () => {
    const turn = {status: "queued", result: {}} as Turn;
    const view = render(<ConversationReply turn={turn} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在排队");
    view.rerender(<ConversationReply turn={{...turn, status: "running", result: {activity: {phase: "waiting_model", completed_reads: 2}}}} />);
    expect(screen.getByRole("status")).toHaveTextContent("资料已读取，正在等待模型回答");
    view.rerender(<ConversationReply turn={{...turn, status: "failed", error_code: "CONVERSATION_MODEL_FAILED", error_message: "模型请求已超过总等待时限，结果尚不确定。"}} />);
    expect(screen.getByRole("status")).toHaveTextContent("你的问题仍保留");
    expect(screen.getByRole("alert")).toHaveTextContent("未自动");
  });
});
