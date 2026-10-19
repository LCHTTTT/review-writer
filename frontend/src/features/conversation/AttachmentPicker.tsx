import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { meQuery } from "../../api/queries";
import { useUiText } from "../../i18n/useUiText";
import { useSharedUploads } from "../library/sharedUploads";
import type { Turn } from "./types";

export function AttachedFiles({ files }: {files: Turn["attachments"]}) {
  const {text} = useUiText();
  return files?.length ? <div className="conversation-attachments">{files.map(file => <a className="badge" key={file.id} target="_blank" rel="noreferrer" href={file.paper_id ? `/api/v1/library/papers/${encodeURIComponent(file.paper_id)}/pdf` : `/api/v1/conversation/attachments/${file.id}`}>{file.name || text("用户附件", "User attachment")}</a>)}</div> : null;
}

export function AttachmentPicker({ ids, onChange, compact = false, textOnly = false }: { ids: string[]; onChange: (ids: string[]) => void; compact?: boolean; textOnly?: boolean }) {
  const {text} = useUiText();
  const identity = useQuery(meQuery);
  const {store, rows} = useSharedUploads(identity.data?.user_id);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const help = textOnly ? text("上传文字附件供对话参考，不自动纳入综述。", "Upload text attachments for discussion, not automatic inclusion.") : text("上传文献或附件供对话参考，不自动纳入综述。PDF 解析可能计费。", "Upload papers or attachments for discussion, not automatic inclusion. PDF parsing may incur charges.");
  return <div className={`conversation-attachments${compact ? " conversation-upload-tool" : ""}`}>
    <button type="button" className={`button button-quiet${compact ? " conversation-upload-icon" : ""}`} aria-label={text("上传文献或附件", "Upload papers or attachments")} title={help} disabled={!store || pending} onClick={() => input.current?.click()}>
      {compact ? <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 16V4m-4 4 4-4 4 4M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></svg> : text("添加文件", "Attach files")}
    </button>
      <input ref={input} aria-label={text("上传对话附件", "Upload chat attachments")} type="file" multiple accept={textOnly ? ".txt,.md,.csv,.json" : ".pdf,.txt,.md,.csv,.json"} disabled={!store || pending} style={{display:"none"}} onChange={async event => {
        const files = Array.from(event.target.files || []); event.target.value = "";
        if (!store || !files.length) return;
        if (files.length + ids.length > 8) { setError(text("每条消息最多 8 个附件。", "At most 8 attachments per message.")); return; }
        setPending(true); setError(""); const attached = [...ids];
        try { for (const file of files) { try { const job = await store.upload(file); attached.push(job.id); onChange([...attached]); } catch (err) { setError(err instanceof Error ? err.message : String(err)); } } }
        finally {setPending(false);}
      }}/>
    {pending ? <span role="status">{text("上传中…", "Uploading…")}</span> : null}
    {ids.map(id => <span className="badge" key={id}>{rows.find(row => row.jobId === id)?.name || text("已保存附件", "Saved attachment")} <button type="button" className="button button-quiet" aria-label={text("移除附件", "Remove attachment")} onClick={() => onChange(ids.filter(value => value !== id))}>×</button></span>)}
    {textOnly ? <small>{text("文字附件供对话参考，不自动纳入综述；论文请使用上方的 PDF 上传。", "Text attachments are for discussion; upload papers using the PDF control above.")}</small> : compact ? <small title={help}>{text("PDF 解析可能计费", "PDF parsing may incur charges")}</small> : <small>{text("附件供本轮讨论使用，不自动纳入综述。PDF 入库解析可能产生费用；请在“本项目文献”中选择，或明确告诉 AI 纳入。文本仅保存，不执行文件内指令。", "Attachments are for discussion, not automatically included in the review. PDF ingestion may incur charges. Select papers under Project papers or explicitly ask AI to include them. Text instructions are not executed.")}</small>}
    {error ? <p className="message message-error">{error} <Link to="/library">{text("到文献库核对上传", "Check library uploads")}</Link></p> : null}
  </div>;
}
