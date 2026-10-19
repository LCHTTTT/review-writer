import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { EditableManuscript } from "../draft/EditableManuscript";
import { DraftManuscriptFields } from "../draft/DraftManuscriptFields";
import type { DialogueParagraph } from "../draft/ParagraphDialogue";
import { apiRequest } from "../../api/client";
import { meQuery } from "../../api/queries";
import { ErrorState } from "../../components/ErrorState";
import { MarkdownView } from "../../components/MarkdownView";
import { useFormDraft } from "../../hooks/useFormDraft";
import { useUiText } from "../../i18n/useUiText";
import type { ArtifactRead, ResultCard } from "./types";

export function ResultViewer({ card, endpoint, projectId, close, expand, select, refresh }: { card: ResultCard; endpoint: string; projectId: string; close: () => void; expand: () => void; select: (card: ResultCard) => void; refresh: () => Promise<unknown> }) {
  const { text, language } = useUiText();
  const apiLanguage = language === "en" ? "en" : "zh";
  const textual = ["md", "json"].includes(card.format);
  const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState(false);
  useEffect(() => { setOffset(0); }, [language, card.id]);
  const content = useQuery({ queryKey: ["conversation-artifact", projectId, card.id, "readable", language, offset], enabled: textual,
    queryFn: () => apiRequest<ArtifactRead>(`${endpoint}/artifacts/${card.id}?offset=${offset}&limit=20000&view=readable&language=${apiLanguage}`) });
  const versions = useQuery({ queryKey: ["conversation-versions", projectId, card.logical_name],
    queryFn: () => apiRequest<{ items: ResultCard[] }>(`${endpoint}/versions/${card.logical_name}`) });
  return <section className="conversation-viewer panel" aria-label={text("成果查看区", "Result viewer")}>
    <header><div><h2>{card.label}</h2><p className="muted">{content.data?.current === false || !card.current ? text("历史版本，只读", "Historical, read-only") : text("已保存版本", "Saved version")}</p></div>
      <div><button className="button button-quiet" onClick={expand}>{text("展开阅读", "Expand")}</button><button className="button button-secondary" onClick={close}>{text("关闭查看区", "Close viewer")}</button></div></header>
    <label>{text("成果版本", "Result version")}<select value={card.id} onChange={e => { const found = versions.data?.items.find(a => a.id === e.target.value); if (found) select(found); }}>
      {(versions.data?.items || [card]).map(a => <option key={a.id} value={a.id}>{a.current ? text("当前", "Current") : text("历史", "History")} · {a.created_at.slice(0, 19)}</option>)}</select></label>
    {versions.error ? <ErrorState error={versions.error} onRetry={() => void versions.refetch()} /> : null}
    {card.logical_name === "draft/manuscript.md" && card.current && content.data?.current !== false ? <button className="button button-secondary" onClick={() => setEditing(value => !value)}>{editing ? text("返回查看", "Read") : text("手动编辑初稿", "Edit Draft manually")}</button> : null}
    {editing ? <DraftEditor projectId={projectId} artifactId={card.id} refresh={refresh} /> : <>
      {content.error ? <ErrorState error={content.error} onRetry={() => void content.refetch()} /> : null}
      {textual && content.isPending ? <p role="status">{text("正在读取成果…", "Loading result…")}</p> : null}
      {textual ? <MarkdownView content={content.data?.text} /> :
        ["png", "jpg", "jpeg", "webp"].includes(card.format) ? <img src={card.url} alt={card.label} /> : <p>{text("打开或下载这个版本的文件。", "Open or download this version.")}</p>}
      {textual && content.data && (offset > 0 || !content.data.complete) ? <div className="conversation-actions"><small>{offset + 1}–{Math.min(offset + 20000, content.data.total_chars)} / {content.data.total_chars}</small>
        <button className="button button-secondary" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20000))}>{text("上一段", "Previous")}</button>
        <button className="button button-secondary" disabled={content.data.next_offset === null} onClick={() => setOffset(content.data!.next_offset!)}>{text("继续读取", "Read more")}</button></div> : null}
    </>}
    {card.format !== "json" ? <a className="button button-quiet" href={card.url} download={`${card.label}.${card.format}`}>{text("下载完整文件", "Download complete file")}</a> : null}
  </section>;
}

function DraftEditor({ projectId, artifactId, refresh }: { projectId: string; artifactId: string; refresh: () => Promise<unknown> }) {
  const { text } = useUiText();
  const identity = useQuery(meQuery);
  const baseline = useQuery({ queryKey: ["conversation-draft-edit", projectId, artifactId],
    queryFn: () => apiRequest<{ revision: number; draft_artifact_id: string; first_draft_md: string; manuscript_preview_md?: string;
      paragraphs: DialogueParagraph[]; manuscript_fields?: { title: string; keywords: string[] };
      legacy_manuscript_fields?: { title: string; keywords: string[] } | null;
      freshness?: { editing_blocked?: boolean; upstream_stale: boolean; stale_paragraph_ids?: string[] } }>(`/api/v1/projects/${projectId}/draft`) });
  const legacy = useFormDraft<{ text: string } | null>(`conversation:${projectId}:draft-edit`, null);
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const recordEdit = useCallback((key: string, value: boolean) => setDirty(previous => previous[key] === value ? previous : { ...previous, [key]: value }), []);
  const refetch = async () => { await baseline.refetch(); await refresh(); };
  if (baseline.error) return <ErrorState error={baseline.error} onRetry={() => void baseline.refetch()} />;
  if (!baseline.data || !identity.data) return <p role="status">{text("读取编辑基线…", "Loading editing baseline…")}</p>;
  const data = baseline.data;
  const mismatched = data.draft_artifact_id !== artifactId;
  const blocked = mismatched || (data.freshness?.editing_blocked ?? data.freshness?.upstream_stale ?? false);
  const manuscript = (keywords?: ReactNode) => <EditableManuscript content={data.manuscript_fields ? (data.manuscript_preview_md ?? data.first_draft_md).replace(/^#\s+[^\n]+\n?/m, "") : data.manuscript_preview_md ?? data.first_draft_md}
    keywords={keywords} paragraphs={data.paragraphs ?? []} userId={identity.data.user_id} projectId={projectId} revision={data.revision}
    disabled={blocked} refresh={refetch} onEditingChange={recordEdit} staleParagraphIds={data.freshness?.stale_paragraph_ids} />;
  return <div className="conversation-editor">
    <p className="message message-warning">{text("与初稿页共用逐段编辑和暂存；不会修改上游章节或自动批准终稿。讨论读取已保存版本。", "Paragraph editing and scratch are shared with Draft. Upstream chapters and approval are unchanged; discussion reads saved text.")}</p>
    {blocked ? <p role="alert">{text("初稿版本或来源已变化，暂不能在这里保存；已有输入保留，请核对最新成果。", "The Draft or its sources changed. Saving here is disabled; your input is preserved.")}</p> : null}
    {Object.values(dirty).some(Boolean) ? <p role="status">{text("有未保存修改", "Unsaved changes")}</p> : null}
    {legacy.value ? <details><summary>{text("旧整稿暂存仍保留，请先备份", "Previous full-document scratch is retained; back it up")}</summary>
      <textarea readOnly aria-label={text("旧整稿暂存备份", "Previous document scratch backup")} value={legacy.value.text} rows={10} /></details> : null}
    {data.manuscript_fields && !blocked ? <DraftManuscriptFields userId={identity.data.user_id} projectId={projectId} revision={data.revision}
      fields={data.manuscript_fields} legacy={data.legacy_manuscript_fields} refresh={refetch} onEditingChange={value => recordEdit("fields", value)}
      renderFields={(title, keywords) => <>{title}{manuscript(keywords)}</>} /> : manuscript()}
  </div>;
}
