import { jobIsActive, useJob } from "../../hooks/useJob";
import { useUiText } from "../../i18n/useUiText";
import type { Job } from "../../api/types";
import { statusNames } from "./labels";
import { LinkedTask, TaskCard } from "./TaskCard";
import { ScreeningResults } from "./ScreeningResults";
import { MarkdownView } from "../../components/MarkdownView";
import { OutlineChoiceCard } from "./OutlineChoiceCard";
import { ArtifactFiles } from "./ArtifactFiles";
import type { ResultCard } from "./types";

const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
type ParagraphEditPreview = {paragraph_id: string; status: string; original_text: string; candidate_text: string; truncated?: boolean};

export function ExecutionCard({ initial, refresh, onOpen }: { initial: Job; refresh: () => Promise<unknown>; onOpen?: (card: ResultCard) => void }) {
  const { text } = useUiText();
  const query = useJob(initial.id);
  const job = query.data || initial;
  const state = (job.result || {}) as { status?: string; notice?: string; next_step?: {label: string; label_en: string; message: string}; scope?: { stop_at?: string; auto_adopt?: boolean; actions?: string[] };
    result_artifacts?: ResultCard[]; pending?: { job_id?: string; business_job_ids?: string[] }; completed?: Array<{ action?: string; exported?: {artifact_id?: string; outline_md?: string;
      matrix_sync?: {selected_paper_count?: unknown}; fact_enrichment_summary?: Record<string, unknown>;
      edit_preview?: ParagraphEditPreview}; job_id?: string; business_job_ids?: string[] }> };
  const analysisSteps = (state.completed || []).filter(step => ["discovery.confirm", "planning.analyze"].includes(step.action || ""));
  const selectedCount = count([...analysisSteps].reverse().find(step => step.exported?.matrix_sync)?.exported?.matrix_sync?.selected_paper_count);
  const facts = [...analysisSteps].reverse().find(step => step.exported?.fact_enrichment_summary)?.exported?.fact_enrichment_summary;
  const processingCounts = [
    ["分析完成", "Analysis complete", count(facts?.complete_count)],
    ["部分完成", "Partially complete", count(facts?.partial_count)],
    ["信息有限", "Limited information", count(facts?.limited_count)],
    ["处理失败", "Failed", count(facts?.failed_count)],
    ["待处理", "Pending", count(facts?.pending_count)],
  ] as const;
  const countsAvailable = selectedCount !== undefined || processingCounts.some(([, , value]) => value !== undefined);
  const retrieval = state.scope?.actions?.length === 1 && state.scope.actions[0] === "discovery.search";
  const adoption = (state.scope?.actions?.length || 0) > 1 && state.scope?.actions?.at(-1) === "discovery.confirm" && state.scope.actions.slice(0, -1).every(action => action === "discovery.selection");
  const screening = [...(state.completed || [])].reverse().find(step => step.action === "discovery.search" && step.exported?.artifact_id);
  const outline = [...(state.completed || [])].reverse().find(step => step.action === "planning.recommend_outline" && step.exported?.outline_md);
  const lastAction = state.completed?.at(-1)?.action || state.scope?.actions?.at(-1);
  const chooseOutline = job.status === "succeeded" && !!job.project_id && ["discovery.confirm", "planning.analyze"].includes(lastAction || "");
  const adoptionNotice = job.status === "succeeded"
    ? text("推荐文献已纳入，筛选与文献分析已完成。", "Recommended papers are included; screening and paper analysis are complete.")
    : jobIsActive(job.status)
      ? text("正在纳入推荐文献并分析，完成后可选择大纲。", "Including and analyzing recommended papers; outline choices follow completion.")
      : text("文献纳入与分析未完成，请查看下方任务状态。", "Inclusion and analysis are unfinished; see the task status below.");
  const notice = job.status === "succeeded" && state.status === "completed" && state.notice?.startsWith("本次授权操作已完成")
    ? text("本次操作已完成。", "This operation is complete.") : state.notice;
  const childIds = [...new Set([state.pending?.job_id, ...(state.pending?.business_job_ids || []),
    ...(state.completed || []).flatMap(step => [step.job_id, ...(step.business_job_ids || [])])].filter((id): id is string => !!id))];
  const byDocument = new Map<string, ResultCard>();
  for (const card of state.result_artifacts || []) {
    const previous = byDocument.get(card.logical_name);
    if (!previous || card.current || (!previous.current && card.created_at > previous.created_at)) byDocument.set(card.logical_name, card);
  }
  const files = [...byDocument.values()];
  const edits = new Map<string, ParagraphEditPreview>();
  for (const step of state.completed || []) {
    const edit = step.exported?.edit_preview;
    if (edit) edits.set(edit.paragraph_id, edit);
  }
  return <article className="conversation-execution-card">
    <h3>{text("任务进展", "Task progress")}</h3>
    {adoption ? <p>{adoptionNotice}</p> : retrieval ? <p>{job.status === "succeeded" ? text("检索已完成，请决定要纳入哪些文献。", "Retrieval is complete; decide which papers to include.") : jobIsActive(job.status) ? text("正在检索文献…", "Retrieving papers…") : text("检索未完成，请查看任务提示。", "Retrieval is unfinished; see the task details.")}</p> : null}
    {notice && !chooseOutline ? <p role="status" className={state.status === "paused" ? "message message-warning" : "muted"}>{notice}</p> : null}
    {chooseOutline ? <OutlineChoiceCard /> : null}
    {countsAvailable ? <p className="muted" aria-label={text("文献处理统计", "Paper processing summary")}>
      {[...(selectedCount === undefined ? [] : [text(`本项目纳入 ${selectedCount} 篇`, `${selectedCount} papers included in this project`)]),
        ...processingCounts.filter(([, , value]) => value !== undefined && value > 0).map(([zh, en, value]) => text(`${zh} ${value} 篇`, `${en}: ${value} papers`))].join(" · ") || text("尚无已完成的文献分析。", "No completed paper analysis yet.")}
    </p> : null}
    {outline?.exported?.outline_md ? <details><summary>{text("查看本次推荐大纲", "View this outline recommendation")}</summary><MarkdownView content={outline.exported.outline_md} /></details> : null}
    <ArtifactFiles ids={files.map(card => card.id)} artifacts={files} onOpen={onOpen} />
    {[...edits.values()].map(edit => <details className="conversation-edit-preview" key={edit.paragraph_id}>
      <summary>{text("查看本次段落修改", "Review this paragraph edit")} · {edit.status === "accepted" ? text("已保存", "Saved") : edit.status === "kept_original" ? text("原文未变", "Original retained") : edit.status === "pending" ? text("候选，尚未保存", "Candidate, not saved") : text("需核对，未保存", "Review needed, not saved")}</summary>
      <h4>{text("修改前", "Before")}</h4><MarkdownView content={edit.original_text}/>
      <h4>{text("修改后", "After")}</h4><MarkdownView content={edit.candidate_text || edit.original_text}/>
      {edit.truncated ? <p className="muted">{text("此处为节选，请打开初稿查看完整内容。", "Excerpt only. Open the Draft for the full text.")}</p> : null}
    </details>)}
    {job.status === "succeeded" && state.next_step && !chooseOutline ? <p className="muted">{text("下一步", "Next")}：{text(state.next_step.label, state.next_step.label_en)}
      {text("，你可以直接回复你的决定或修改要求。", "; reply with your decision or requested changes.")}</p> : null}
    <details className="conversation-execution-details" open={(retrieval || adoption) && jobIsActive(job.status)}><summary>{text("执行进度与任务", "Execution progress & tasks")} · {text(...(statusNames[job.status] || [job.status, job.status]))}</summary><TaskCard initial={job} onComplete={refresh} />
    {childIds.map(id => <LinkedTask key={id} id={id} refresh={refresh} />)}</details>
    {screening?.exported?.artifact_id && job.project_id ? <ScreeningResults artifactId={screening.exported.artifact_id} projectId={job.project_id} /> : null}
  </article>;
}
