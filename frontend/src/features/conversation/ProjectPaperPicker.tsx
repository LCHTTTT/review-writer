import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiRequest, jsonBody } from "../../api/client";
import { meQuery } from "../../api/queries";
import { LocalizedError } from "../../components/LocalizedError";
import { jobIsActive, useJob } from "../../hooks/useJob";
import { useFormDraft } from "../../hooks/useFormDraft";
import { useUiText } from "../../i18n/useUiText";
import { useSharedUploads } from "../library/sharedUploads";
import type { Job } from "../../api/types";
import { TaskCard } from "./TaskCard";

type Paper = {paper_id: string; title: string; selected: boolean; parsed: boolean};
type Choices = {revision: number; items: Paper[]; selected_paper_ids: string[]; confirmed_paper_ids: string[];
  selected_items?: Array<{paper_id: string; title: string; available: boolean}>;
  total: number; next_offset: number | null; selection_pending: boolean};

function UploadedPaper({ id, name, onReady }: {id: string; name: string; onReady: (id: string) => void}) {
  const { text } = useUiText();
  const job = useJob(id);
  const delivered = useRef(false);
  const result = job.data?.result;
  const paperId = String(result?.paper_id || result?.duplicate_paper_id || "");
  useEffect(() => {
    if (job.data?.status === "succeeded" && paperId && !delivered.current) {
      delivered.current = true; onReady(paperId);
    }
  }, [job.data?.status, paperId, onReady]);
  return <li><strong>{name}</strong> · {job.data?.status === "succeeded" && paperId
    ? text("已入库，已勾选待保存", "In library; checked for saving")
    : jobIsActive(job.data?.status) || job.isPending ? text("正在上传／处理…", "Uploading / processing…")
    : <LocalizedError error={job.error || job.data?.error_message || text("上传未完成，请到文献库核对。", "Upload incomplete. Check the library.")} />}</li>;
}

export function ProjectPaperPicker({ projectId, extra }: {projectId: string; extra?: ReactNode}) {
  const { text } = useUiText();
  const client = useQueryClient();
  const identity = useQuery(meQuery);
  const { store } = useSharedUploads(identity.data?.user_id);
  const [open, setOpen] = useState(false);
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const selection = useFormDraft<{changes: Record<string, boolean>; revision: number | null}>(`project-papers:${projectId}`, {changes: {}, revision: null});
  const { changes, revision } = selection.value;
  const [uploads, setUploads] = useState<Array<{id: string; name: string}>>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<unknown>(null);
  const [notice, setNotice] = useState("");
  const completedUploads = useRef(new Set<string>());
  const changesRef = useRef(changes); changesRef.current = changes;
  const dialog = useRef<HTMLDialogElement>(null);
  const endpoint = `/api/v1/projects/${encodeURIComponent(projectId)}/discovery/papers`;
  const choices = useQuery({queryKey: ["project-papers", projectId, query, offset],
    queryFn: () => apiRequest<Choices>(`${endpoint}?query=${encodeURIComponent(query)}&offset=${offset}`)});
  const data = choices.data;
  useEffect(() => {
    if (!open || !dialog.current) return;
    const previous = document.activeElement as HTMLElement | null;
    if (typeof dialog.current.showModal === "function") dialog.current.showModal();
    else dialog.current.setAttribute("open", "");
    return () => { dialog.current?.close?.(); previous?.focus(); };
  }, [open]);
  const change = (id: string, selected: boolean) => {
    const next = {...changesRef.current, [id]: selected};
    changesRef.current = next;
    selection.set({revision: revision ?? data?.revision ?? null, changes: next}); setNotice("");
  };
  const refresh = async () => {
    await Promise.all([client.invalidateQueries({queryKey: ["project-papers", projectId]}),
      client.invalidateQueries({queryKey: ["discovery", projectId]}),
      client.invalidateQueries({queryKey: ["planning", projectId]}),
      client.invalidateQueries({queryKey: ["conversation-overview", projectId]}),
      client.invalidateQueries({queryKey: ["projects"]})]);
  };
  const save = useMutation({mutationFn: (submitted: Record<string, boolean>) => apiRequest<Choices>(endpoint, {method: "PATCH",
      ...jsonBody({revision: revision ?? data?.revision ?? 0,
        add_ids: Object.keys(submitted).filter(key => submitted[key]), remove_ids: Object.keys(submitted).filter(key => !submitted[key])})}),
    onSuccess: async (result, submitted) => {
      const remaining = Object.fromEntries(Object.entries(changesRef.current).filter(([key, value]) => submitted[key] !== value));
      changesRef.current = remaining;
      if (Object.keys(remaining).length) selection.set({changes: remaining, revision: result.revision});
      else selection.clear();
      setNotice(text("文献选择已保存。需要更新大纲与写作时，再确认并开始分析。", "Selection saved. Confirm and start analysis when you want to update downstream work.")); await refresh();}});
  const confirm = useMutation({mutationFn: () => apiRequest<{matrix_analysis_job?: Job}>(endpoint.replace(/\/papers$/, "/confirm"),
      {method: "POST", ...jsonBody({revision: data?.revision ?? 0})}),
    onSuccess: async () => {setNotice(text("文献选择已确认；分析任务已提交，已有稿件仍保留。", "Selection confirmed; analysis submitted. Existing manuscript retained.")); await refresh();}});
  const analysisJob = useJob(confirm.data?.matrix_analysis_job?.id || "");
  const count = data ? new Set([...data.selected_paper_ids.filter(id => changes[id] !== false), ...Object.keys(changes).filter(id => changes[id])]).size : 0;
  return <div className="conversation-paper-tool">
    <button className="button button-quiet conversation-upload-icon" aria-label={text("选择或上传项目文献", "Choose or upload project papers")} title={text(`选择或上传项目文献 · 已选择 ${count} 篇；PDF 解析可能计费`, `Choose or upload project papers · ${count} selected; PDF parsing may incur charges`)} onClick={() => setOpen(true)}>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 16V4m-4 4 4-4 4 4M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4" /></svg>
    </button>
    {open ? createPortal(<dialog ref={dialog} className="project-paper-dialog" aria-labelledby="project-papers-title" onCancel={event => {event.preventDefault(); if (!save.isPending) setOpen(false);}}>
      <header><div><span className="step-label">{text("文献选择", "Paper selection")}</span><h2 id="project-papers-title">{text("本项目文献", "Project papers")}</h2></div>
        <button className="button button-quiet" disabled={save.isPending} onClick={() => setOpen(false)}>{text("关闭", "Close")}</button></header>
      <p className="muted">{text("从共享文献库勾选，或上传 PDF 后纳入。仅保存选择不会生成内容，也不会覆盖已有稿件。", "Choose from your library or upload PDFs. Saving choices does not generate content or overwrite a manuscript.")}</p>
      <p className="muted">{data ? text(`已选择 ${count} 篇 · 已用于分析 ${data.confirmed_paper_ids.length} 篇`, `${count} selected · ${data.confirmed_paper_ids.length} used in analysis`) : text("正在读取文献选择…", "Loading paper selection…")}</p>
      {data?.selected_items?.length ? <details className="conversation-selected-papers"><summary>{text("已纳入的文献", "Included papers")} · {data.selected_items.length}</summary><ul>{data.selected_items.map(paper => <li key={paper.paper_id}><strong>{paper.title || paper.paper_id}</strong>{!paper.available ? <small>{text("文献库中已不可用", "Unavailable in library")}</small> : null}</li>)}</ul></details> : null}
      <div className="project-paper-tools"><form onSubmit={event => {event.preventDefault(); setQuery(queryInput.trim()); setOffset(0);}}>
        <input aria-label={text("搜索文献标题或编号", "Search paper title or ID")} value={queryInput} onChange={event => setQueryInput(event.target.value)} placeholder={text("搜索标题或文献编号", "Search title or paper ID")} />
        <button className="button button-secondary" type="submit">{text("搜索", "Search")}</button></form>
        <label className="button button-secondary">{uploading ? text("上传中…", "Uploading…") : text("上传 PDF", "Upload PDFs")}
          <input aria-label={text("上传项目文献 PDF", "Upload project PDFs")} hidden type="file" accept=".pdf" multiple disabled={!store || uploading || save.isPending || confirm.isPending} onChange={async event => {
            const files = Array.from(event.target.files || []); event.target.value = "";
            if (!store || !files.length) return;
            setUploading(true); setUploadError(null);
            try { for (const file of files) {try {const job = await store.upload(file); setUploads(value => [...value, {id: job.id, name: file.name}]);} catch(error) {setUploadError(error);}} }
            finally {setUploading(false);}
          }} /></label></div>
      <small className="muted">{text("PDF 使用现有解析服务，可能产生费用；入库后自动勾选，点击保存后才纳入本项目。", "PDF ingestion may incur charges. Completed uploads are checked automatically; save to include them in this project.")}</small>
      {uploads.length ? <ul className="project-paper-uploads">{uploads.map(upload => <UploadedPaper key={upload.id} {...upload} onReady={id => {
        if (!completedUploads.current.has(upload.id)) {completedUploads.current.add(upload.id); change(id, true); void refresh();}
      }} />)}</ul> : null}
      {uploadError ? <LocalizedError error={uploadError} /> : null}
      <div className="project-paper-list" aria-label={text("可选文献", "Available papers")}>
        {data?.items.map(paper => <label className="project-paper-row" key={paper.paper_id}>
          <input type="checkbox" checked={changes[paper.paper_id] ?? data.selected_paper_ids.includes(paper.paper_id)} disabled={save.isPending || confirm.isPending} onChange={event => change(paper.paper_id, event.target.checked)} />
          <span><strong>{paper.title || paper.paper_id}</strong><small>{paper.paper_id} · {paper.parsed ? text("已解析", "Parsed") : text("解析尚未完成，可先选择", "Parsing incomplete; selection allowed")}</small></span></label>)}
        {!choices.isPending && data?.total === 0 ? <p>{query ? text("没有匹配的文献，请调整搜索。", "No matching papers. Adjust your search.") : text("文献库暂无论文，可以先上传 PDF。", "Your library is empty. Upload PDFs to begin.")}</p> : null}
        {choices.isPending ? <p role="status">{text("正在读取文献…", "Loading papers…")}</p> : null}
      </div>
      <div className="project-paper-pagination"><button className="button button-quiet" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 50))}>{text("上一页", "Previous")}</button>
        <span>{text(`共 ${data?.total ?? 0} 篇`, `${data?.total ?? 0} papers`)}</span>
        <button className="button button-quiet" disabled={data?.next_offset == null} onClick={() => setOffset(data!.next_offset!)}>{text("下一页", "Next")}</button></div>
      {choices.error || save.error || confirm.error ? <p role="alert"><LocalizedError error={choices.error || save.error || confirm.error} /> <button className="button button-quiet" onClick={() => {save.reset(); confirm.reset(); void choices.refetch().then(result => {if (result.data) selection.set({changes: changesRef.current, revision: result.data.revision});});}}>{text("读取最新名单，保留勾选", "Reload list, keep choices")}</button></p> : null}
      {selection.storageFailed ? <p role="alert">{text("浏览器无法暂存选择，请保存后再离开页面。", "Browser could not retain choices. Save before leaving.")}</p> : null}
      {Object.keys(changes).length > 200 ? <p role="alert">{text("每次最多保存 200 篇文献的变更，请分批选择。", "Save changes to up to 200 papers at a time. Select in batches.")}</p> : null}
      {notice ? <p role="status" className="message message-success">{notice}</p> : null}
      {confirm.data?.matrix_analysis_job ? <TaskCard initial={confirm.data.matrix_analysis_job} onComplete={refresh} /> : null}
      <p className="muted">{text("确认并开始分析可能调用模型并产生费用；改变文献范围后，下游会提示按需更新，不会删除已有内容。", "Confirming analysis may incur model charges. Corpus changes mark downstream work for updating; existing content is not deleted.")}</p>
      {extra}
      <footer><span>{text(`已选择 ${count} 篇`, `${count} selected`)}{Object.keys(changes).length ? text(" · 有未保存选择", " · Unsaved choices") : ""}</span>
        <button className="button button-primary" disabled={!data || !Object.keys(changes).length || Object.keys(changes).length > 200 || save.isPending || uploading || confirm.isPending} onClick={() => save.mutate({...changes})}>{text("保存文献选择", "Save paper selection")}</button>
        <button className="button button-secondary" disabled={!data || !count || !!Object.keys(changes).length || save.isPending || confirm.isPending || uploading || jobIsActive(analysisJob.data?.status ?? confirm.data?.matrix_analysis_job?.status)} onClick={() => confirm.mutate()}>{text("确认并开始分析", "Confirm & start analysis")}</button>
        <Link className="button button-quiet" to={`/discovery?project=${encodeURIComponent(projectId)}`}>{text("查看筛选详情", "View screening")}</Link></footer>
    </dialog>, document.body) : null}
  </div>;
}
