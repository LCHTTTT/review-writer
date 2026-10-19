import { useEffect, useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { Job } from "../../api/types";
import { LocalizedError } from "../../components/LocalizedError";
import { MarkdownView } from "../../components/MarkdownView";
import { jobIsActive } from "../../hooks/useJob";
import { useUiText } from "../../i18n/useUiText";
import type { ActionProposal, ResultCard } from "./types";
import { ArtifactFiles } from "./ArtifactFiles";
import { LinkedTask, TaskCard } from "./TaskCard";

export function ActionCard({ action, turnId, endpoint, ready, refresh, onOpen }: { action: ActionProposal; turnId: string; endpoint: string; ready: boolean; refresh: () => Promise<unknown>; onOpen?: (card: ResultCard) => void }) {
  const { text } = useUiText();
  const path = `${endpoint}/actions/${turnId}/${action.id}`;
  const state = useQuery({ queryKey: ["conversation-action", turnId, action.id], queryFn: () => apiRequest<{ job: Job | null }>(path),
    refetchInterval: query => jobIsActive(query.state.data?.job?.status) ? 1500 : false });
  const run = useMutation({ mutationFn: () => apiRequest<Job>(path, { method: "POST" }),
    onSuccess: async () => { await state.refetch(); await refresh(); } });
  const job = state.data?.job;
  const seen = useRef(false);
  useEffect(() => { if (job && !jobIsActive(job.status) && !seen.current) { seen.current = true; void refresh(); } }, [job?.status, refresh]);
  const outcome = job?.result?.outcome as { job?: Job; matrix_analysis_job?: Job; synthesis_job_id?: string } | undefined;
  const files = (job?.result?.result_artifacts || []) as ResultCard[];
  return <article className="conversation-action-card">
    <h3>{action.label}</h3><p className="muted">{action.notice}</p>
    {Object.keys(action.arguments).length ? <details><summary>{text("查看操作内容", "Inspect operation")}</summary>
      {typeof action.arguments.outline_md === "string" ? <MarkdownView content={action.arguments.outline_md} /> : <pre>{JSON.stringify(action.arguments, null, 2)}</pre>}
    </details> : null}
    {action.preview?.candidate_text ? <details><summary>{text("查看原文与候选", "Inspect original and candidate")}</summary>
      {action.preview.original_text ? <><h4>{text("原文", "Original")}</h4><MarkdownView content={action.preview.original_text} /></> : null}
      <h4>{text("候选（尚未采用）", "Candidate (not adopted)")}</h4><MarkdownView content={action.preview.candidate_text} />
      {action.preview.preview_truncated ? <p className="message message-warning">{text("这里只展示部分内容，请在原阶段查看完整候选。", "This is a partial preview; inspect the full candidate in its stage.")}</p> : null}
      {action.preview.validation_errors?.length ? <p className="message message-warning">{text("候选包含校验提示；采用仍须通过原阶段校验。", "Candidate has validation findings; adoption still uses the original stage checks.")}</p> : null}
    </details> : null}
    {job && jobIsActive(job.status) ? <TaskCard initial={job} onComplete={refresh} /> : null}
    {!job ? <><p className="muted">{text("可以直接在对话中说明是否采用，以及希望调整的内容。", "Reply with whether to adopt this proposal and any changes you want.")}</p><details><summary>{text("快捷执行（可选）", "Quick execution (optional)")}</summary><button className="button button-secondary" disabled={!ready || run.isPending || state.isPending || !!state.error} onClick={() => run.mutate()}>{text("采用并执行这个操作", "Adopt and execute this operation")}</button></details></> :
      <p role="status">{jobIsActive(job.status) ? text("操作已提交，正在执行…", "Operation submitted; executing…") :
        job.result?.execution_completed ? text("操作已完成；生成任务及成果状态见下方。", "Operation completed; see generation and result status below.") : text("操作未完成，请核对提示与实际成果。", "Operation incomplete; review the error and actual results.")}</p>}
    {job?.error_message ? <p role="alert" className="message message-error">{job.error_message}</p> : null}
    {state.error || run.error ? <LocalizedError error={state.error || run.error} /> : null}
    {outcome?.job ? <TaskCard initial={outcome.job} onComplete={refresh} /> : null}
    {outcome?.matrix_analysis_job ? <TaskCard initial={outcome.matrix_analysis_job} onComplete={refresh} /> : null}
    {outcome?.synthesis_job_id ? <LinkedTask id={outcome.synthesis_job_id} refresh={refresh} /> : null}
    <ArtifactFiles ids={files.map(card => card.id)} artifacts={files} onOpen={onOpen} />
  </article>;
}
