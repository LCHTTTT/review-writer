import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiRequest, jsonBody, newIdempotencyKey } from "../../api/client";
import { meQuery, projectsQuery } from "../../api/queries";
import { ErrorState } from "../../components/ErrorState";
import { LocalizedError } from "../../components/LocalizedError";
import { ConversationReply } from "./ConversationReply";
import { jobIsActive } from "../../hooks/useJob";
import { useFormDraft } from "../../hooks/useFormDraft";
import { useUiText } from "../../i18n/useUiText";
import type { Job } from "../../api/types";
import type { History, Overview, ResultCard } from "./types";
import { PreparationConversation } from "./PreparationConversation";
import { DiscussionPicker } from "./DiscussionPicker";
import { AttachmentPicker, AttachedFiles } from "./AttachmentPicker";
import { ProjectModelPicker } from "./ProjectModelPicker";
import { ActionCard } from "./ActionCard";
import { ExecutionCard } from "./ExecutionCard";
import { refreshProjectResults } from "./refreshProject";
import { TaskCard } from "./TaskCard";
import { ResultViewer } from "./ResultViewer";
import { ArtifactFiles } from "./ArtifactFiles";
import { ProjectPaperPicker } from "./ProjectPaperPicker";
import "./conversation.css";

export function ConversationPage() {
  const { text } = useUiText();
  const projects = useQuery(projectsQuery);
  const identity = useQuery(meQuery);
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project") || "";
  const selected = projects.data?.items.find(p => p.project_id === projectId);
  const page = useRef<HTMLElement>(null);
  useEffect(() => {
    const update = () => {
      if (page.current) page.current.style.setProperty("--conversation-room", `${Math.max(360, window.innerHeight - page.current.getBoundingClientRect().top - 40)}px`);
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    const top = document.querySelector(".topbar");
    if (top) observer?.observe(top);
    window.addEventListener("resize", update);
    return () => { observer?.disconnect(); window.removeEventListener("resize", update); };
  }, []);
  const projectControls = <div className="conversation-contextbar">
      <label htmlFor="conversation-project">{text("当前项目", "Current project")}</label>
      <select id="conversation-project" value={selected ? projectId : ""} onChange={e => {
        const next = new URLSearchParams(); if (e.target.value) next.set("project", e.target.value); setParams(next);
      }}><option value="">{text("选择项目", "Choose a project")}</option>
        {projects.data?.items.map(p => <option key={p.project_id} value={p.project_id} title={p.topic}>{p.slug}</option>)}
      </select>
      <Link className="button button-secondary" to="/workspace">{text("新建项目", "New project")}</Link>
    </div>;
  return <main ref={page} className="page-container conversation-page">
    {projects.error || identity.error ? <ErrorState error={projects.error || identity.error} onRetry={() => { void projects.refetch(); void identity.refetch(); }} /> :
      projectId && !selected && !projects.isPending ? <section className="panel"><header className="conversation-header">{projectControls}</header><h2>{text("项目不可用", "Project unavailable")}</h2>
        <p>{text("链接中的项目不存在或无权访问，未自动切换到其他项目。请重新选择。", "This project is unavailable. No other project was selected automatically.")}</p></section> :
      selected && identity.data ? <ProjectConversation projectControls={projectControls} key={`${identity.data.user_id}:${projectId}:${params.get("session") || ""}`} sessionId={params.get("session") || ""} projectId={projectId} projectName={selected.slug} /> :
        identity.data && !projects.isPending ? <PreparationConversation projectControls={projectControls} key={`${identity.data.user_id}:preparation:${params.get("session") || ""}`} sessionId={params.get("session") || ""} /> : <section className="panel conversation-empty" role="status">{text("正在读取项目…", "Loading projects…")}</section>}
  </main>;
}

function ProjectConversation({ projectId, projectName, sessionId, projectControls }: { projectId: string; projectName: string; sessionId: string; projectControls: ReactNode }) {
  const { text } = useUiText();
  const client = useQueryClient();
  const endpoint = `/api/v1/projects/${encodeURIComponent(projectId)}/conversation`;
  const discussionKey = sessionId ? `${projectId}:${sessionId}` : projectId;
  const sessionQuery = sessionId ? `session_id=${encodeURIComponent(sessionId)}` : "";
  const [selection, setSelection] = useState<ResultCard | null>(null);
  const [wide, setWide] = useState(false);
  const [older, setOlder] = useState<History["turns"]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [newReply, setNewReply] = useState(false);
  const [modelPending, setModelPending] = useState(false);
  const webSearch = useFormDraft(`conversation:${discussionKey}:web-search`, false);
  const message = useFormDraft(`conversation:${discussionKey}:message`, "");
  const attachments = useFormDraft<string[]>(`conversation:${discussionKey}:attachments`, []);
  const attachmentRef = useRef(attachments.value); attachmentRef.current = attachments.value;
  const delivery = useFormDraft<"follow_up" | "steer">(`conversation:${discussionKey}:delivery`, "follow_up");
  const initialRequest = useRef(newIdempotencyKey());
  const request = useFormDraft(`conversation:${discussionKey}:request`, initialRequest.current);
  const messageRef = useRef(message.value); messageRef.current = message.value;
  const log = useRef<HTMLDivElement>(null);
  const bottom = useRef(true);
  const overview = useQuery({ queryKey: ["conversation-overview", projectId], queryFn: () => apiRequest<Overview>(`${endpoint}/overview`),
    refetchInterval: q => q.state.data?.jobs.some(j => jobIsActive(j.status)) ? 5000 : false });
  const history = useQuery({ queryKey: sessionId ? ["conversation-history", projectId, sessionId] : ["conversation-history", projectId], queryFn: () => apiRequest<History>(`${endpoint}/messages${sessionQuery ? "?" + sessionQuery : ""}`),
    refetchInterval: q => q.state.data?.turns.some(t => jobIsActive(t.status)) ? 1500 : false });
  const turns = [...new Map([...older, ...(history.data?.turns || [])].map(t => [t.id, t])).values()];
  const active = history.data?.turns.find(t => jobIsActive(t.status));
  const token = turns.map(t => `${t.id}:${t.status}:${t.result?.reply || ""}:${t.result?.tool_events?.length || 0}`).join("|");
  const refresh = async () => {
    await Promise.all([history.refetch(), overview.refetch(), refreshProjectResults(client, projectId)]);
  };
  const send = useMutation({ mutationFn: (submitted: { value: string; id: string; objectId: string; original: string; delivery: "follow_up" | "steer"; attachmentIds: string[]; webSearch: boolean }) =>
    apiRequest<Job>(`${endpoint}/messages`, { method: "POST", headers: { "Idempotency-Key": submitted.id },
      ...jsonBody({ message: submitted.value, object_id: submitted.objectId, web_search: submitted.webSearch, ...(submitted.attachmentIds.length ? {attachment_ids: submitted.attachmentIds} : {}), ...(sessionId ? {session_id: sessionId} : {}), ...(submitted.delivery === "steer" ? { delivery: "steer" } : {}) }) }),
    onSuccess: async (_job, submitted) => {
      if (messageRef.current === submitted.original) message.clear();
      attachments.set(attachmentRef.current.filter(id => !submitted.attachmentIds.includes(id)));
      request.set(newIdempotencyKey()); await refresh();
    } });
  const cancel = useMutation({ mutationFn: (id: string) => apiRequest(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }), onSuccess: refresh });
  const loadOlder = useMutation({ mutationFn: (before: string) => apiRequest<History>(`${endpoint}/messages?before=${encodeURIComponent(before)}${sessionQuery ? "&" + sessionQuery : ""}`),
    onSuccess: data => { setOlder(current => [...data.turns, ...current]); setCursor(data.next_cursor); } });
  const submit = () => {
    if (!message.value.trim() || send.isPending || modelPending) return;
    bottom.current = true;
    request.set(request.value); // Persist the operation key before submission, including lost responses.
    send.mutate({ value: message.value.trim(), original: message.value, id: request.value,
      objectId: selection && ["md", "json"].includes(selection.format) ? selection.id : "", delivery: delivery.value, attachmentIds: [...attachments.value], webSearch: webSearch.value });
  };
  const pendingMessage = send.variables && (send.isPending || (send.data && !turns.some(turn => turn.id === send.data.id))) ? send.variables.value : "";
  useEffect(() => {
    if (bottom.current && log.current) { log.current.scrollTop = log.current.scrollHeight; setNewReply(false); }
    else if (log.current) setNewReply(true);
  }, [token, pendingMessage]);
  const nextCursor = older.length ? cursor : history.data?.next_cursor;
  const error = overview.error || history.error;
  return <div className={`conversation-workspace${selection ? " has-viewer" : ""}${wide ? " viewer-wide" : ""}`}>
    <section className="conversation-chat panel">
      <header className="conversation-header"><div className="conversation-title"><span className="step-label">{text("综述协作", "Review collaboration")}</span><h1>{projectName}</h1></div>
        <div className="conversation-header-controls">{projectControls}<DiscussionPicker endpoint={endpoint} sessionId={sessionId} /></div>
        {overview.data?.jobs.some(j => !j.job_type.startsWith("conversation.")) ? <details className="conversation-background"><summary>{text("活动任务", "Active tasks")} · {overview.data.jobs.filter(j => !j.job_type.startsWith("conversation.")).length}</summary>
          {overview.data.jobs.filter(j => !j.job_type.startsWith("conversation.")).map(job => <TaskCard key={job.id} initial={job} onComplete={refresh} />)}</details> : null}</header>
      {error ? <ErrorState error={error} onRetry={() => void refresh()} /> : null}
      <div className="conversation-log" ref={log} role="region" aria-label={text("项目对话记录", "Project conversation history")}
        onScroll={() => { if (log.current) { bottom.current = log.current.scrollHeight - log.current.scrollTop - log.current.clientHeight < 60; if (bottom.current) setNewReply(false); } }}>
        {nextCursor ? <button className="button button-quiet" disabled={loadOlder.isPending} onClick={() => loadOlder.mutate(nextCursor)}>{text("加载更早对话", "Load earlier messages")}</button> : null}
        {!turns.length && !history.isPending ? <div className="empty-state"><h2>{text("一起推进这篇综述", "Let's work on this review")}</h2>
          <p>{text("可以问“现在还差什么”，或打开一个成果讨论具体问题。", "Ask what remains, or open a result to discuss a specific issue.")}</p>
          {[text("现在还差什么？", "What remains?"), text("看看当前大纲是否合理，先不要修改。", "Review the current outline without changing it.")].map(value =>
            <button className="button button-secondary" key={value} onClick={() => { message.set(value); request.set(newIdempotencyKey()); }}>{value}</button>)}
        </div> : null}
        {turns.map(turn => <article className="conversation-turn" key={turn.id}>
          <div className="chat-message chat-message-user"><small>{text("你", "You")}</small><p>{turn.message}</p><AttachedFiles files={turn.attachments} /></div>
          <div className="chat-message chat-message-assistant"><small>AI</small>
            <ConversationReply turn={turn} allowResume={!active && turn.id === turns.at(-1)?.id} />
            {turn.result?.executions?.map(job => <ExecutionCard key={job.id} initial={job} refresh={refresh} onOpen={setSelection} />)}
            {turn.result?.actions?.map(action => <ActionCard key={action.id} action={action} turnId={turn.id} endpoint={endpoint}
              ready={turn.status === "succeeded"} refresh={refresh} onOpen={setSelection} />)}
            {turn.result?.artifact_ids?.length ? <ArtifactFiles ids={turn.result.artifact_ids} artifacts={overview.data?.artifacts || []} onOpen={card => setSelection(card)} /> : null}
          </div>
        </article>)}
        {pendingMessage ? <article className="conversation-turn conversation-submitting" aria-live="polite">
          <div className="chat-message chat-message-user"><small>{text("你", "You")}</small><p>{pendingMessage}</p></div>
          <p role="status" className="conversation-submit-status">{send.isPending ? text("正在提交…", "Submitting…") : text("已提交，正在同步任务状态…", "Submitted, syncing task status…")}</p>
        </article> : null}
      </div>
      {newReply ? <button className="button button-secondary" onClick={() => { bottom.current = true; if (log.current) log.current.scrollTop = log.current.scrollHeight; setNewReply(false); }}>{text("有新回复 ↓", "New reply ↓")}</button> : null}
      <div className="conversation-composer">
        {selection ? <p>{text("当前查看", "Viewing")}：{selection.label} · {selection.current ? text("当前版本", "Current") : text("历史版本（只读）", "Historical, read-only")}
          <button className="button button-quiet" onClick={() => setSelection(null)}>{text("移除对象", "Clear object")}</button></p> : null}
        <label className="sr-only" htmlFor="project-chat-message">{text("输入项目问题", "Project question")}</label>
        <textarea id="project-chat-message" rows={4} maxLength={12000} value={message.value}
          placeholder={text("输入问题或讨论想法…", "Ask a question or discuss an idea…")}
          onChange={e => { message.set(e.target.value); request.set(newIdempotencyKey()); }}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }} />
        <div className="conversation-composer-tools">
          <ProjectPaperPicker projectId={projectId} extra={<details className="conversation-extra-attachments"><summary>{text("补充对话附件（可选）", "Additional discussion attachments (optional)")}</summary><AttachmentPicker ids={attachments.value} onChange={attachments.set} textOnly /></details>} />
          <ProjectModelPicker projectId={projectId} current={overview.data?.model_tier || ""} refresh={refresh} onPendingChange={setModelPending} disabled={send.isPending} />
          <label className="conversation-web-toggle" title={webSearch.value ? text("检索时允许联网", "Online retrieval permitted") : text("仅检索文献库", "Library search only")}><input type="checkbox" role="switch" checked={webSearch.value}
            onChange={event => { webSearch.set(event.target.checked); request.set(newIdempotencyKey()); }} />
            <span>{text("联网检索", "Web search")}</span></label>
          <div className="conversation-toolbar-actions">
            {active ? <button className="button button-secondary" disabled={cancel.isPending} onClick={() => cancel.mutate(active.id)}>{text("停止回复", "Stop reply")}</button> : null}
            <button className="button button-primary" disabled={send.isPending || modelPending || !message.value.trim() || !!error} onClick={submit}>{send.isPending ? text("提交中…", "Submitting…") : text("发送", "Send")}</button>
          </div>
        </div>
        {active || overview.data?.jobs.some(job => job.job_type === "conversation.plan") ? <details className="conversation-delivery-options"><summary>{text("发送方式", "Delivery options")}</summary><label className="conversation-delivery">{text("新消息处理方式", "New message delivery")} <select value={delivery.value} onChange={e => { delivery.set(e.target.value as "follow_up" | "steer"); request.set(newIdempotencyKey()); }}>
          <option value="follow_up">{text("追加后续请求（保留当前任务）", "Queue follow-up (keep current tasks)")}</option><option value="steer">{text("调整尚未执行的操作／采用", "Adjust operations not yet executed")}</option>
        </select></label></details> : null}
        <div className="conversation-actions"><small className="muted">{text("Enter 发送 · Shift+Enter 换行", "Enter to send · Shift+Enter for a new line")}</small>
        </div>
        {message.storageFailed ? <p role="alert">{text("暂存不可用，请保留输入。", "Scratch storage unavailable; preserve your input.")}</p> : null}
        {send.error || cancel.error || loadOlder.error ? <p className="message message-error" role="alert"><LocalizedError error={send.error || cancel.error || loadOlder.error} /></p> : null}
      </div>
    </section>
    {selection ? <ResultViewer key={selection.id} card={selection} endpoint={endpoint} projectId={projectId}
      close={() => { setSelection(null); setWide(false); }} expand={() => setWide(value => !value)} select={setSelection} refresh={refresh} /> : null}
  </div>;
}
