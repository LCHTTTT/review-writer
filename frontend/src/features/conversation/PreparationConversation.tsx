import { useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiRequest, jsonBody, newIdempotencyKey } from "../../api/client";
import { taxonomyProfilesQuery } from "../../api/queries";
import type { Job, Project } from "../../api/types";
import { ErrorState } from "../../components/ErrorState";
import { LocalizedError } from "../../components/LocalizedError";
import { ConversationReply } from "./ConversationReply";
import { ModelOptions } from "../../components/ModelOptions";
import { useFormDraft } from "../../hooks/useFormDraft";
import { jobIsActive } from "../../hooks/useJob";
import { useUiText } from "../../i18n/useUiText";
import type { History } from "./types";
import { DiscussionPicker } from "./DiscussionPicker";
import { AttachmentPicker, AttachedFiles } from "./AttachmentPicker";

type Fields = { slug: string; topic: string; taxonomy_profile: string; model_tier: string };

export function PreparationConversation({ sessionId = "", projectControls }: { sessionId?: string; projectControls?: ReactNode }) {
  const { text } = useUiText();
  const navigate = useNavigate();
  const client = useQueryClient();
  const endpoint = "/api/v1/conversation/preparation";
  const scope = sessionId ? `preparation:${sessionId}` : "preparation";
  const message = useFormDraft(`conversation:${scope}:message`, "");
  const attachments = useFormDraft<string[]>(`conversation:${scope}:attachments`, []);
  const attachmentRef = useRef(attachments.value); attachmentRef.current = attachments.value;
  const initial = useRef(newIdempotencyKey());
  const identity = useFormDraft(`conversation:${scope}:request`, initial.current);
  const form = useFormDraft<Fields>("new-project", { slug: "", topic: "", taxonomy_profile: "chemistry_general", model_tier: "" });
  const created = useFormDraft<string>(`conversation:${scope}:created-project`, "");
  const [showCreate, setShowCreate] = useState(false);
  const profiles = useQuery(taxonomyProfilesQuery);
  const history = useQuery({ queryKey: sessionId ? ["conversation-preparation", sessionId] : ["conversation-preparation"], queryFn: () => apiRequest<History>(`${endpoint}/messages?limit=50${sessionId ? "&session_id=" + encodeURIComponent(sessionId) : ""}`),
    refetchInterval: query => query.state.data?.turns.some(turn => jobIsActive(turn.status)) ? 1500 : false });
  const messageRef = useRef(message.value); messageRef.current = message.value;
  const send = useMutation({ mutationFn: (input: { message: string; key: string; attachmentIds: string[] }) => apiRequest<Job>(`${endpoint}/messages`, {
      method: "POST", headers: { "Idempotency-Key": input.key }, ...jsonBody({ message: input.message, ...(input.attachmentIds.length ? {attachment_ids: input.attachmentIds} : {}), ...(sessionId ? { session_id: sessionId } : {}) }) }),
    onSuccess: async (_, input) => { if (messageRef.current === input.message) message.clear(); attachments.set(attachmentRef.current.filter(id => !input.attachmentIds.includes(id))); identity.set(newIdempotencyKey()); await history.refetch(); } });
  const active = history.data?.turns.find(turn => jobIsActive(turn.status));
  const cancel = useMutation({ mutationFn: (id: string) => apiRequest(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }), onSuccess: () => history.refetch() });
  const create = useMutation({ mutationFn: async () => {
      let projectId = created.value;
      if (!projectId) {
        const project = await apiRequest<Project>("/api/v1/projects", { method: "POST", ...jsonBody({ ...form.value, model_tier: form.value.model_tier || null }) });
        projectId = project.project_id;
        created.set(projectId); // Binding retries never create another project.
      }
      const ids = history.data?.turns.filter(turn => !jobIsActive(turn.status)).map(turn => turn.id) || [];
      if (ids.length || sessionId) await apiRequest(`${endpoint}/bind`, { method: "POST", ...jsonBody({ project_id: projectId, turn_ids: ids, ...(sessionId ? { session_id: sessionId } : {}) }) });
      return projectId;
    }, onSuccess: async projectId => { form.clear(); created.clear(); await client.invalidateQueries({ queryKey: ["projects"] }); await client.invalidateQueries({ queryKey: ["conversation-preparation"] }); navigate(`/conversation?project=${encodeURIComponent(projectId)}${sessionId ? "&session=" + encodeURIComponent(sessionId) : ""}`); } });
  const submit = () => { if (message.value.trim() && !send.isPending) send.mutate({ message: message.value.trim(), key: identity.value, attachmentIds: [...attachments.value] }); };
  const change = (key: keyof Fields, value: string) => form.set({ ...form.value, [key]: value });
  return <section className="conversation-chat conversation-preparation panel">
    <header className="conversation-header"><div><span className="step-label">{text("准备讨论", "Preparation")}</span><h1>{text("先聊聊你的综述", "Start with your review idea")}</h1></div>
      <div className="conversation-header-controls">{projectControls}<DiscussionPicker endpoint={endpoint} sessionId={sessionId} /><button className="button button-secondary" onClick={() => setShowCreate(value => !value)}>{text("在这里新建项目", "Create a project here")}</button></div></header>
    <div className="conversation-log" role="region" aria-label={text("准备对话记录", "Preparation history")}>
      {!history.data?.turns.length ? <div className="empty-state"><h2>{text("从一个研究问题开始", "Start with a research question")}</h2><p>{text("告诉我研究主题、希望比较什么，以及已经有哪些论文。也可以不使用模型，直接创建项目。", "Describe your topic, comparisons and papers, or create a project without using a model.")}</p></div> : null}
      {history.error ? <ErrorState error={history.error} onRetry={() => void history.refetch()} /> : null}
      {history.data?.turns.map(turn => <article className="conversation-turn" key={turn.id}><div className="chat-message chat-message-user"><small>{text("你", "You")}</small><p>{turn.message}</p><AttachedFiles files={turn.attachments} /></div>
        <div className="chat-message chat-message-assistant"><small>AI</small><ConversationReply turn={turn} allowResume={turn.id === history.data?.turns.at(-1)?.id} />
          {turn.result?.project_proposal ? <article className="conversation-action-card"><h3>{text("项目创建建议（尚未创建）", "Project proposal (not created)")}</h3><strong>{turn.result.project_proposal.slug}</strong><p>{turn.result.project_proposal.topic}</p>
            <button className="button button-secondary" disabled={turn.status !== "succeeded" || !!created.value} onClick={() => { form.set({ ...form.value, ...turn.result.project_proposal, model_tier: form.value.model_tier }); setShowCreate(true); }}>{text("使用建议填写表单", "Use suggestion in form")}</button></article> : null}
        </div></article>)}
      {showCreate || created.value ? <form className="conversation-create-form" onSubmit={event => { event.preventDefault(); create.mutate(); }}><h2>{text("创建并绑定项目", "Create and bind project")}</h2>
        <label>{text("项目名称", "Project name")}<input value={form.value.slug} maxLength={96} required disabled={!!created.value || create.isPending} onChange={event => change("slug", event.target.value)} /></label>
        <label>{text("研究主题", "Research topic")}<textarea value={form.value.topic} rows={3} maxLength={10000} disabled={!!created.value || create.isPending} onChange={event => change("topic", event.target.value)} /></label>
        <div className="conversation-create-options"><label>{text("分类", "Taxonomy")}<select value={form.value.taxonomy_profile} disabled={!!created.value || create.isPending} onChange={event => change("taxonomy_profile", event.target.value)}>
          {!profiles.data?.items.length ? <option value={form.value.taxonomy_profile}>{form.value.taxonomy_profile}</option> : profiles.data.items.map(profile => <option key={profile.id} value={profile.id}>{text(profile.label_zh, profile.label_en)}</option>)}</select></label>
        <label>{text("项目模型", "Project model")}<select value={form.value.model_tier} disabled={!!created.value || create.isPending} onChange={event => change("model_tier", event.target.value)}><option value="">{text("服务器默认", "Server default")}</option><ModelOptions selected={form.value.model_tier} /></select></label></div>
        {history.error ? <p className="muted">{text("准备记录暂时无法读取，仍可创建项目；未读取的记录不会被自动绑定。", "Preparation history is unavailable. You can still create; unread messages are not automatically bound.")}</p> : null}
        {created.value ? <p className="message message-info">{text("项目已创建，等待完成准备记录绑定；重试不会再创建项目。", "Project created; awaiting history binding. Retry does not create another project.")}</p> : null}
        <button className="button button-primary" type="submit" disabled={create.isPending || send.isPending || !!active || (!created.value && !form.value.slug.trim())}>{text(created.value ? "重试绑定并进入" : "创建并进入对话", created.value ? "Retry binding" : "Create & open conversation")}</button>
        {create.error ? <LocalizedError error={create.error} /> : null}</form> : null}
    </div>
    <div className="conversation-composer">
      <label className="sr-only" htmlFor="preparation-message">{text("输入综述需求", "Describe review needs")}</label>
      <textarea id="preparation-message" rows={3} maxLength={12000} value={message.value} onChange={event => { message.set(event.target.value); identity.set(newIdempotencyKey()); }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); } }} />
      <div className="conversation-composer-tools"><AttachmentPicker ids={attachments.value} onChange={attachments.set} compact />
        <div className="conversation-toolbar-actions">{active ? <button className="button button-secondary" disabled={cancel.isPending} onClick={() => cancel.mutate(active.id)}>{text("停止回复", "Stop reply")}</button> : null}<button className="button button-primary" disabled={send.isPending || !message.value.trim() || !!history.error || !!created.value} onClick={submit}>{text("发送", "Send")}</button></div>
      </div>
      <p className="conversation-submit-status">{text("讨论可选；创建项目无需先完成对话", "Discussion is optional; create directly whenever ready")}</p>
      {send.error || cancel.error ? <LocalizedError error={send.error || cancel.error} /> : null}{message.storageFailed || form.storageFailed ? <p role="alert">{text("暂存不可用，请保留输入。", "Scratch storage unavailable; preserve your input.")}</p> : null}
    </div>
  </section>;
}
