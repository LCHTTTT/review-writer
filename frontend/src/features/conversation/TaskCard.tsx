import { useEffect, useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";
import type { Job } from "../../api/types";
import { LocalizedError } from "../../components/LocalizedError";
import { jobIsActive, useJob } from "../../hooks/useJob";
import { useUiText } from "../../i18n/useUiText";
import { statusNames } from "./labels";
import { DiscoveryJobProgress } from "../discovery/DiscoveryJobProgress";

const jobLabels: Record<string, [string, string]> = {
  "discovery.search": ["检索与筛选", "Retrieval & screening"], "matrix.enrich": ["文献分析", "Paper analysis"],
  "discovery.acquire": ["下载与解析文献", "Acquire and parse paper"], "planning.topic-outline": ["推荐大纲", "Outline recommendation"],
  "planning.blueprint": ["章节规划", "Chapter planning"], "sections.generate": ["章节正文", "Section drafts"],
  "figures.redraw": ["图像重绘", "Figure redraw"], "final.overview": ["总览图候选", "Overview candidate"],
  "draft.optimize": ["初稿内容处理", "Draft content"], "final.build": ["终稿组装", "Final assembly"],
  "final.export": ["Word导出", "Word export"], "final.pdf": ["PDF导出", "PDF export"],
  "conversation.plan": ["阶段接续", "Stage continuation"], "conversation.action": ["执行阶段操作", "Stage operation"],
};

export function LinkedTask({ id, refresh }: { id: string; refresh: () => Promise<unknown> }) {
  const query = useJob(id);
  return query.data ? <TaskCard initial={query.data} onComplete={refresh} /> : query.error ? <LocalizedError error={query.error} /> : null;
}

export function TaskCard({ initial, onComplete }: { initial: Job; onComplete: () => Promise<unknown> }) {
  const { text } = useUiText();
  const query = useJob(initial.id);
  const job = query.data || initial;
  const finished = useRef(false);
  useEffect(() => { if (!jobIsActive(job.status) && !finished.current) { finished.current = true; void onComplete(); } }, [job.status, onComplete]);
  const cancel = useMutation({ mutationFn: () => apiRequest(`/api/v1/jobs/${job.id}/cancel`, { method: "POST" }), onSuccess: () => query.refetch() });
  return <article className="conversation-task"><strong>{text("后台任务", "Background task")} · {text(...(jobLabels[job.job_type] || [job.job_type, job.job_type]))}</strong>
    {job.job_type === "discovery.search" ? <DiscoveryJobProgress job={job} /> : <p>{text(...(statusNames[job.status] || [job.status, job.status]))}{job.progress_total > 0 ? ` · ${job.progress_current}/${job.progress_total}` : ""}</p>}
    {job.available_actions?.includes("cancel") ? <button className="button button-secondary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{job.job_type === "conversation.plan" ? text("停止继续（保留已提交任务）", "Stop continuation (keep submitted tasks)") : text("取消这个任务", "Cancel this task")}</button> : null}
    {job.error_message ? <p role="alert">{job.error_message}</p> : null}
    {query.error || cancel.error ? <LocalizedError error={query.error || cancel.error} /> : null}
  </article>;
}
