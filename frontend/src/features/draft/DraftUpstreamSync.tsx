import {useState} from "react";
import {useMutation} from "@tanstack/react-query";
import {apiRequest, jsonBody} from "../../api/client";
import {LocalizedError} from "../../components/LocalizedError";
import {useUiText} from "../../i18n/useUiText";

type Preview = {section_id: string; draft_artifact_id: string; sections_artifact_id: string;
  changes: {paragraph_id: string; original_text: string; candidate_text: string}[];
  conflicts: {paragraph_id: string; conflicts: {base_text: string; upstream_text: string; draft_edits: {text: string}[]}[]}[]};

export function DraftUpstreamSync({projectId, draftArtifactId, sections, requireSaved, refresh}: {
  projectId: string; draftArtifactId: string; sections: {section_id: string; title: string}[];
  requireSaved: () => void; refresh: () => Promise<unknown>;
}) {
  const {text} = useUiText();
  const [sectionId, setSectionId] = useState(sections[0]?.section_id || "");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [saved, setSaved] = useState(false);
  const base = `/api/v1/projects/${encodeURIComponent(projectId)}/draft/upstream-sync/`;
  const inspect = useMutation({mutationFn: (id: string) => apiRequest<Preview>(base + encodeURIComponent(id)),
    onSuccess: value => {setPreview(value); setSaved(false);}});
  const save = useMutation({mutationFn: async (value: Preview) => {
    requireSaved();
    return apiRequest(base + encodeURIComponent(value.section_id), {method: "POST", ...jsonBody({
      expected_draft_artifact_id: value.draft_artifact_id, expected_sections_artifact_id: value.sections_artifact_id})});
  }, onSuccess: async () => {setPreview(null); setSaved(true); await refresh();}});
  const stale = preview?.draft_artifact_id !== draftArtifactId;
  const busy = inspect.isPending || save.isPending;
  return <details className="draft-upstream-sync">
    <summary>{text("查看上游章节变化与同步", "Review upstream chapter changes")}</summary>
    <p className="muted">{text("只预览不会改变初稿，也不调用模型。保存只合并指定章节正文，保留其他手改；结构或引用变化需另行处理。", "Preview does not write or call a model. Saving merges only this chapter's prose and preserves other manual edits; structural and citation changes need separate review.")}</p>
    {!sections.length ? <p role="status">{text("当前初稿尚无可同步章节。", "No chapters are available to synchronize.")}</p> : <>
      <label>{text("同步章节", "Chapter to synchronize")} <select value={sectionId} disabled={busy} onChange={event => {
        setSectionId(event.target.value); setPreview(null); setSaved(false); inspect.reset(); save.reset();
      }}>{sections.map(section => <option key={section.section_id} value={section.section_id}>{section.title || section.section_id}</option>)}</select></label>
      <button type="button" className="button button-secondary" disabled={busy || !sectionId} onClick={() => {setPreview(null); setSaved(false); save.reset(); inspect.mutate(sectionId);}}>{inspect.isPending ? text("正在比较…", "Comparing…") : text("预览同步差异", "Preview differences")}</button>
    </>}
    {inspect.error || save.error ? <p role="alert" className="message message-error"><LocalizedError error={inspect.error || save.error} /></p> : null}
    {saved ? <p role="status">{text("指定章节正文已保存。科学来源和其他上游失效状态仍需单独核对；这不等于已批准初稿。", "Chapter prose saved. Scientific sources and other stale inputs still need review; the Draft is not approved.")}</p> : null}
    {preview ? <div>
      {stale ? <p role="status">{text("初稿版本已变化，请重新预览；旧差异不能保存。", "The Draft changed. Refresh the preview before saving.")}</p> : null}
      {!preview.changes.length && !preview.conflicts.length ? <p role="status">{text("没有需要合并的正文变化。", "No prose changes to merge.")}</p> : null}
      {preview.changes.map(change => <details key={change.paragraph_id}><summary>{change.paragraph_id} · {text("正文差异", "Prose changes")}</summary>
        <h4>{text("当前已保存初稿", "Current saved Draft")}</h4><pre style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{change.original_text}</pre>
        <h4>{text("合并候选（尚未保存）", "Merge candidate (not saved)")}</h4><pre style={{whiteSpace: "pre-wrap", overflowWrap: "anywhere"}}>{change.candidate_text}</pre>
      </details>)}
      {preview.conflicts.length ? <><p role="alert">{text("上游变化与手工修改重叠，禁止自动保存。以下两版均保留，请先决定如何处理。", "Upstream changes overlap manual edits. Automatic saving is blocked; both versions are retained for review.")}</p>
        {preview.conflicts.map(row => <details key={row.paragraph_id}><summary>{row.paragraph_id} · {text("修改冲突", "Edit conflict")}</summary>{row.conflicts.map((conflict, index) => <div key={index}>
          <h4>{text("共同基线", "Shared baseline")}</h4><pre style={{whiteSpace: "pre-wrap"}}>{conflict.base_text}</pre>
          <h4>{text("上游修改", "Upstream edit")}</h4><pre style={{whiteSpace: "pre-wrap"}}>{conflict.upstream_text}</pre>
          <h4>{text("你的手工修改", "Your manual edit")}</h4><pre style={{whiteSpace: "pre-wrap"}}>{conflict.draft_edits.map(edit => edit.text).join("\n")}</pre>
        </div>)}</details>)}</> : null}
      <button type="button" className="button button-primary" disabled={busy || stale || !!preview.conflicts.length || !preview.changes.length} onClick={() => save.mutate(preview)}>{text("保存此章节同步正文", "Save this chapter's merged prose")}</button>
    </div> : null}
  </details>;
}
