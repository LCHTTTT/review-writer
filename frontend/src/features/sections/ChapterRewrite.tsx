import {useMutation, useQuery} from "@tanstack/react-query";
import {apiRequest, jsonBody, newIdempotencyKey} from "../../api/client";
import {meQuery} from "../../api/queries";
import type {Job} from "../../api/types";
import {MarkdownView} from "../../components/MarkdownView";
import {LocalizedError} from "../../components/LocalizedError";
import {jobIsActive, useJob} from "../../hooks/useJob";
import {useUiText} from "../../i18n/useUiText";
import {useDraftScratch} from "../draft/useDraftScratch";

type Candidate = {candidate_artifact_id: string; base_sections_artifact_id: string; original_section: {draft_md: string}; section: {draft_md: string}};
export function ChapterRewrite(props: {projectId: string; sectionId: string; indexArtifactId: string; refresh: () => Promise<unknown>}) {
  const me = useQuery(meQuery);
  return me.data ? <Editor key={`${me.data.user_id}:${props.projectId}:${props.sectionId}`} {...props} userId={me.data.user_id} /> : null;
}
function Editor({projectId, sectionId, indexArtifactId, userId, refresh}: {projectId: string; sectionId: string; indexArtifactId: string; userId: string; refresh: () => Promise<unknown>}) {
  const {text} = useUiText();
  const key = `${userId}:${projectId}:upstream-rewrite:${sectionId}`;
  const [message, setMessage, storageFailed] = useDraftScratch(key + ":message", "");
  const [requestKey, setRequestKey] = useDraftScratch(key + ":request", newIdempotencyKey());
  const [jobId, setJobId] = useDraftScratch(key + ":job", "");
  const [submitted, setSubmitted] = useDraftScratch<{message: string; base: string; key: string} | null>(key + ":submitted", null);
  const job = useJob(jobId);
  const candidate = job.data?.status === "succeeded" ? job.data.result as Candidate | undefined : undefined;
  const base = `/api/v1/projects/${encodeURIComponent(projectId)}/sections`;
  const submit = useMutation({mutationFn: () => {
    const snapshot = submitted?.message === message && submitted.key === requestKey ? submitted : {message, base: indexArtifactId, key: requestKey};
    setSubmitted(snapshot);
    return apiRequest<Job>(base + `/${encodeURIComponent(sectionId)}/rewrite-jobs`, {method: "POST", headers: {"Idempotency-Key": snapshot.key}, ...jsonBody({message: snapshot.message, expected_sections_artifact_id: snapshot.base})});
  }, onSuccess: value => {setJobId(value.id); setSubmitted(null); setRequestKey(newIdempotencyKey());}});
  const adopt = useMutation({mutationFn: () => apiRequest(base + `/rewrite-candidates/${encodeURIComponent(candidate!.candidate_artifact_id)}/accept`, {method: "POST"}),
    onSuccess: async () => {await refresh();}});
  const busy = submit.isPending || adopt.isPending || jobIsActive(job.data?.status);
  return <details className="advanced-panel"><summary>{text("改写当前上游整章", "Revise this upstream chapter")}</summary><div className="advanced-panel-body">
    <p>{text("只生成当前整章的候选，不改其他章节或初稿。仍使用原章节写作与证据校验，可能产生模型费用；不支持指定段落、章节增删或硬性费用限制。", "Generates a candidate for this whole chapter only, leaving other chapters and the Draft unchanged. Uses the original writer and source checks; model charges may apply. Paragraph-only edits, structural changes and hard cost limits are not supported.")}</p>
    <label>{text("本章改写要求", "Chapter revision request")}<textarea value={message} maxLength={4000} disabled={busy} onChange={event => {setMessage(event.target.value); if (!submit.error) {setSubmitted(null); setRequestKey(newIdempotencyKey());}}} /></label>
    {storageFailed ? <p role="alert">{text("本机暂存不可用，离开页面前请备份要求。", "Local storage is unavailable. Back up your request before leaving.")}</p> : null}
    <button className="button button-secondary" type="button" disabled={busy || !message.trim()} onClick={() => {adopt.reset(); submit.mutate();}}>{text("生成本章改写候选", "Generate chapter revision candidate")}</button>
    {job.data && jobIsActive(job.data.status) ? <p role="status">{text("章节改写任务进行中，原章节尚未替换。", "Chapter revision is in progress; saved text is unchanged.")}</p> : null}
    {job.data && ["failed", "interrupted", "cancelled"].includes(job.data.status) ? <p role="alert">{text("章节改写未完成，原成果保留。", "Chapter revision did not complete; saved work is retained.")} <LocalizedError error={job.data.error_message || job.data.status} /></p> : null}
    {submit.error ? <p role="status">{text("没有自动重发。若响应丢失，请先核对任务；相同要求重试沿用原请求身份，不会改成新生成。", "No automatic replay. Check tasks if acknowledgement was lost; retrying the same request keeps its original identity.")}</p> : null}
    {submit.error || adopt.error || job.error ? <p role="alert"><LocalizedError error={submit.error || adopt.error || job.error} /></p> : null}
    {candidate?.candidate_artifact_id ? <>
      <h4>{text("生成时的上游章节", "Upstream chapter before revision")}</h4><MarkdownView content={candidate.original_section.draft_md} />
      <h4>{text("改写候选（尚未采用）", "Revision candidate (not adopted)")}</h4><MarkdownView content={candidate.section.draft_md} />
      {candidate.base_sections_artifact_id !== indexArtifactId && !adopt.isSuccess ? <p role="status">{text("上游版本已变化，不能覆盖新成果。", "Upstream work changed; this candidate cannot overwrite it.")}</p> : null}
      <button type="button" className="button button-primary" disabled={busy || adopt.isSuccess || candidate.base_sections_artifact_id !== indexArtifactId} onClick={() => adopt.mutate()}>{text("采用到上游章节", "Adopt into upstream chapter")}</button>
    </> : null}
    {adopt.isSuccess ? <p role="status">{text("已采用到上游章节，初稿正文未改变。请在初稿中另行检查同步差异。", "Adopted into the upstream chapter; Draft prose is unchanged. Review synchronization separately in Draft.")}</p> : null}
  </div></details>;
}
