import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Job } from "../../api/types";
import { fireEvent } from "@testing-library/react";
import { ExecutionCard } from "./ExecutionCard";

vi.mock("../../hooks/useJob", () => ({ useJob: () => ({ data: undefined }), jobIsActive: (status: string) => ["queued", "running"].includes(status) }));
vi.mock("./TaskCard", () => ({ TaskCard: () => null, LinkedTask: () => null }));
afterEach(cleanup);

test("paragraph previews distinguish saved edits and deduplicate the generated candidate", () => {
  const preview = {paragraph_id:"S1-p1", original_text:"Original wording", candidate_text:"Improved wording", status:"pending"};
  render(<ExecutionCard initial={job("succeeded", {completed:[
    {action:"draft.revise_paragraph", exported:{edit_preview:preview}},
    {action:"draft.accept_generated", exported:{edit_preview:{...preview,status:"accepted"}}},
  ]})} refresh={vi.fn()} />);
  expect(screen.getAllByText("Improved wording")).toHaveLength(1);
  expect(screen.getByText("查看本次段落修改 · 已保存")).toBeInTheDocument();
  expect(screen.queryByText("候选，尚未保存",{exact:false})).not.toBeInTheDocument();
});

test("an unsaved candidate is not presented as a saved paragraph", () => {
  render(<ExecutionCard initial={job("succeeded", {completed:[{action:"draft.revise_paragraph",exported:{edit_preview:{paragraph_id:"S1-p1",original_text:"Before",candidate_text:"After",status:"pending",truncated:true}}}]})} refresh={vi.fn()} />);
  expect(screen.getByText("查看本次段落修改 · 候选，尚未保存")).toBeInTheDocument();
  expect(screen.getByText("此处为节选，请打开初稿查看完整内容。")).toBeInTheDocument();
});

const result = {
  status: "completed", next_step: { label: "生成章节规划", label_en: "Generate chapter planning", message: "生成章节规划" },
  result_artifacts: [{ id: "actual-outline", stage: "planning", label: "选定大纲", logical_name: "planning/selected_outline.json",
    current: true, format: "json", created_at: "", sha256: "", url: "/api/v1/artifacts/actual-outline/content" }],
};
const job = (status = "succeeded", extra = {}) => ({ id: "plan", project_id: "p", job_type: "conversation.plan", status,
  result: { ...result, ...extra } } as unknown as Job);

test("completed execution omits raw artifact controls and invites a typed decision", () => {
  render(<ExecutionCard initial={job()} refresh={vi.fn()} />);
  expect(screen.queryByText("查看成果")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "选定大纲" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "填入对话" })).not.toBeInTheDocument();
  expect(screen.getByText(/直接回复你的决定/)).toBeInTheDocument();
});

test("generated PDF and Word show direct download files, not internal JSON", () => {
  const pdf = { ...result.result_artifacts[0], id: "pdf", label: "PDF", logical_name: "final/manuscript.pdf", format: "pdf", url: "/api/v1/artifacts/pdf/content" };
  const word = { ...pdf, id: "word", label: "Word", logical_name: "final/manuscript.docx", format: "docx", url: "/api/v1/artifacts/word/content" };
  render(<ExecutionCard initial={job("succeeded", { result_artifacts: [pdf, word, { ...pdf, id: "internal", label: "发布状态", logical_name: "final/release.json", format: "json" }] })} refresh={vi.fn()} />);
  expect(screen.getByRole("link", { name: "下载 PDF" })).toHaveAttribute("href", pdf.url);
  expect(screen.getByRole("link", { name: "下载 PDF" })).toHaveAttribute("download", "PDF.pdf");
  expect(screen.getByRole("link", { name: "下载 Word" })).toHaveAttribute("href", word.url);
  expect(screen.queryByText("发布状态")).not.toBeInTheDocument();
});

test("readable outline opens the saved viewer without a JSON download or path", () => {
  const open = vi.fn();
  render(<ExecutionCard initial={job()} refresh={vi.fn()} onOpen={open} />);
  fireEvent.click(screen.getByRole("button", { name: "查看 选定大纲" }));
  expect(open).toHaveBeenCalledWith(result.result_artifacts[0]);
  expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
  expect(screen.queryByText("planning/selected_outline.json")).not.toBeInTheDocument();
});

test("multiple versions from a single operation do not duplicate the document card", () => {
  render(<ExecutionCard initial={job("succeeded", { result_artifacts: [
    { ...result.result_artifacts[0], id: "old", current: false }, result.result_artifacts[0],
  ] })} refresh={vi.fn()} onOpen={vi.fn()} />);
  expect(screen.getAllByRole("button", { name: "查看 选定大纲" })).toHaveLength(1);
});

test("paper processing uses actual last summary without duplicating counts or exposing artifacts", () => {
  render(<ExecutionCard initial={job("succeeded", { completed: [
    { action: "discovery.confirm", exported: { matrix_sync: { selected_paper_count: 16 }, fact_enrichment_summary: { complete_count: 10 } } },
    { action: "planning.analyze", exported: { fact_enrichment_summary: { complete_count: 14, partial_count: 1, failed_count: 1, pending_count: 0 } } },
  ] })} refresh={vi.fn()} />);
  expect(screen.getByLabelText("文献处理统计")).toHaveTextContent("本项目纳入 16 篇 · 分析完成 14 篇 · 部分完成 1 篇 · 处理失败 1 篇");
  expect(screen.queryByText("查看成果")).not.toBeInTheDocument();
});

test("missing and invalid counts are not guessed from artifacts or task steps", () => {
  render(<ExecutionCard initial={job("succeeded", { completed: [{ action: "discovery.confirm", exported: {
    matrix_sync: { selected_paper_count: "16" }, fact_enrichment_summary: { complete_count: -1 },
  } }] })} refresh={vi.fn()} />);
  expect(screen.queryByLabelText("文献处理统计")).not.toBeInTheDocument();
});

test("running generation never advertises a completed next operation", () => {
  render(<ExecutionCard initial={job("running")} refresh={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "填入对话" })).not.toBeInTheDocument();
});

test("outline recommendation shows the exact generated outline without pretending it was adopted", () => {
  render(<ExecutionCard initial={job("succeeded", { result_artifacts: [], completed: [{ action: "planning.recommend_outline",
    exported: { outline_md: "# Comparison\n\n## Evidence routes" } }] })} refresh={vi.fn()} />);
  expect(screen.getByText("查看本次推荐大纲")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Evidence routes", hidden: true })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "选定大纲" })).not.toBeInTheDocument();
});

test("existing completed adoption asks for an outline decision without choice buttons", () => {
  render(<ExecutionCard initial={job("succeeded", {
    scope: { actions: ["discovery.selection", "discovery.confirm"] },
    completed: [{ action: "discovery.confirm" }], next_step: { label: "生成推荐大纲", label_en: "Generate recommendation", message: "生成主题推荐大纲" },
    notice: "本次授权操作已完成；下方显示实际成果和下一步，未执行授权范围外的操作。" })} refresh={vi.fn()} />);
  expect(screen.getByRole("region", { name: "大纲选择" })).toBeInTheDocument();
  expect(screen.getByText(/你希望按主题与文献推荐结构/)).toBeInTheDocument();
  expect(screen.getByText(/生成主题推荐大纲.*按时间顺序生成大纲/)).toBeInTheDocument();
  expect(screen.queryByText(/未执行授权范围外/)).not.toBeInTheDocument();
  const choice = within(screen.getByRole("region", { name: "大纲选择" }));
  expect(choice.queryByRole("button")).not.toBeInTheDocument();
  expect(choice.queryByRole("link")).not.toBeInTheDocument();
});

test.each(["running", "failed", "cancelled"])("%s adoption does not advertise completed outline choices", status => {
  render(<ExecutionCard initial={job(status, { scope: { actions: ["discovery.selection", "discovery.confirm"] } })} refresh={vi.fn()} />);
  expect(screen.queryByRole("region", { name: "大纲选择" })).not.toBeInTheDocument();
});

test("manual paper analysis completion also offers outline choices", () => {
  render(<ExecutionCard initial={job("succeeded", { completed: [{ action: "planning.analyze" }] })} refresh={vi.fn()} />);
  expect(screen.getByRole("region", { name: "大纲选择" })).toBeInTheDocument();
});
