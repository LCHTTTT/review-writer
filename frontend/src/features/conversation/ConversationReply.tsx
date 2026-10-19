import { MarkdownView } from "../../components/MarkdownView";
import { LocalizedError } from "../../components/LocalizedError";
import { jobIsActive } from "../../hooks/useJob";
import { useUiText } from "../../i18n/useUiText";
import type { Turn } from "./types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";

function ResumeReply({ id, projectId }: {id: string; projectId: string | null}) {
  const { text } = useUiText();
  const client = useQueryClient();
  const resume = useMutation({mutationFn: () => apiRequest(`/api/v1/jobs/${encodeURIComponent(id)}/retry`, {method: "POST"}),
    onSuccess: async () => {
      const keys = projectId ? [["conversation-history", projectId], ["conversation-overview", projectId]] : [["conversation-preparation"]];
      await Promise.all(keys.map(queryKey => client.invalidateQueries({queryKey})));
    }});
  return <div><button className="button button-secondary" disabled={resume.isPending || resume.isSuccess} onClick={() => resume.mutate()}>
    {resume.isSuccess ? text("已提交继续回复", "Resume submitted") : resume.isPending ? text("正在提交…", "Submitting…") : text("从中断处继续回复", "Resume unfinished reply")}</button>
    <small className="muted">{text("保留已完成操作，仅重试未完成的模型轮次；可能产生模型费用。", "Completed operations are retained. Only unfinished model rounds are retried; model costs may apply.")}</small>
    {resume.error ? <p role="alert"><LocalizedError error={resume.error} /></p> : null}</div>;
}

/** Only persisted phases are progress; elapsed time is not inferred completion. */
export function ConversationReply({ turn, allowResume = false }: {turn: Turn; allowResume?: boolean}) {
  const { text } = useUiText();
  const activity = turn.result?.activity;
  const started = Date.parse(turn.started_at || turn.created_at || "");
  const seconds = Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
  let status = text("正在准备本轮对话…", "Preparing this conversation…");
  if (turn.status === "queued") status = text("对话已提交，正在排队…", "Conversation submitted; waiting for a worker…");
  else if (turn.status === "cancel_requested") status = text("正在停止回复…", "Stopping the reply…");
  else if (turn.job_type === "conversation.control") status = text("停止请求已保存，不等待模型回复。", "Stop request saved; no model response needed.");
  else if (activity?.phase === "reading") status = text("正在读取项目资料…", "Reading project material…");
  else if (activity?.phase === "waiting_model") status = activity.completed_reads
    ? text("资料已读取，正在等待模型回答…", "Material read; waiting for the model reply…")
    : text("正在等待模型理解本轮要求…", "Waiting for the model to interpret your request…");
  if (!jobIsActive(turn.status)) status = turn.status === "cancelled"
    ? text("回复已停止，你的问题仍保留。", "Reply stopped; your question is retained.")
    : text("本轮未取得完整回复，你的问题仍保留。", "No complete reply was obtained; your question is retained.");
  return <>{turn.result?.reply ? <MarkdownView content={turn.result.reply} /> : <p role="status">{status}
    {jobIsActive(turn.status) && seconds > 0 ? <small className="conversation-elapsed">{text(`已用时 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`, `Elapsed ${Math.floor(seconds / 60)}m ${seconds % 60}s`)}</small> : null}</p>}
    {turn.error_message ? <p role="alert" className="message message-error"><LocalizedError error={{code: turn.error_code, message: turn.error_message, toString: () => turn.error_message}} /></p> : null}
    {allowResume && turn.status === "failed" && turn.error_code === "CONVERSATION_MODEL_FAILED" && turn.available_actions?.includes("retry")
      ? <ResumeReply id={turn.id} projectId={turn.project_id} /> : null}</>;
}
