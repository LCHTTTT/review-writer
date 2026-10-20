import {type ReactNode} from 'react';
import {useMutation} from '@tanstack/react-query';
import {apiRequest,jsonBody,newIdempotencyKey} from '../../api/client';
import {LocalizedError} from '../../components/LocalizedError';
import {jobIsActive} from '../../hooks/useJob';
import {useUiText} from '../../i18n/useUiText';
import {ParagraphManualEditor} from './ParagraphManualEditor';
import {PeerReviewReport,type ReviewParagraph} from './PeerReviewReport';
import {PeerReviewProgress,reviewFailureSummary} from './PeerReviewProgress';
import {REVIEW_REVISION_POLICY,type PeerReviewJob,type ReviewRun} from './peerReviewTypes';
import {DRAFT_SCRATCH_PREFIX,hasDraftScratch,useDraftScratch} from './useDraftScratch';
import './peer-review.css';
export type {PeerReviewJob} from './peerReviewTypes';

type Props={projectId:string;userId:string;revision:number;draftArtifactId:string;sections:{section_id:string;title:string}[];
  paragraphs:ReviewParagraph[];job?:PeerReviewJob;blocked:boolean;requireSaved:()=>void;refresh:()=>Promise<unknown>;
  authorApprovedReviewJobId?:string;authorApprovedReviewGroupIds?:string[];
  runs?:ReviewRun[];onSelectRun?:(id:string)=>void;
  onEditingChange?:(key:string,dirty:boolean)=>void;extraContent?:ReactNode};
export function DraftPeerReview({projectId,userId,revision,draftArtifactId,paragraphs,job,blocked,requireSaved,refresh,extraContent,onEditingChange,
  authorApprovedReviewJobId,authorApprovedReviewGroupIds,runs=[],onSelectRun}:Props) {
  const {text}=useUiText();
  const [guidance,setGuidance,failedGuidance]=useDraftScratch<string>(`${userId}:${projectId}:review-revision-guidance`,'');
  const [standard,setStandard,failedStandard]=useDraftScratch<string>(`${userId}:${projectId}:review-revision-standard`,'');
  const retainedInputs:{key:string;value:string}[]=[];
  try {
    const prefix=`${DRAFT_SCRATCH_PREFIX}${userId}:${projectId}:`;
    for(const key of Object.keys(localStorage)) {
      const suffix=key.slice(prefix.length);
      if(!key.startsWith(prefix)||!suffix.includes('review')||suffix.includes('review-revision-')||suffix.endsWith('review-manual')) continue;
      const raw=localStorage.getItem(key); if(!raw) continue;
      let value=raw; try {const decoded:unknown=JSON.parse(raw);value=typeof decoded==='string'?decoded:JSON.stringify(decoded,null,2);}catch{/* Preserve raw input. */}
      retainedInputs.push({key,value});
    }
  }catch{/* Disabled browser storage. */}
  const active=jobIsActive(job?.status); const current=job?.result?.policy===REVIEW_REVISION_POLICY;
  const historical=!!job&&job.source_draft_artifact_id!==draftArtifactId&&job.result?.publication?.draft_artifact_id!==draftArtifactId&&authorApprovedReviewJobId!==job.id;
  const interrupted=!!job&&['failed','cancelled','interrupted'].includes(job.status);
  const rejectedReviewGroups=job?.rejected_group_ids || [];
  const pendingReviewGroups=job?.pending_group_count ?? (job?.result?.revisions || []).filter(group=>
    group.status==='not_saved' && group.patches.length>0
    && !rejectedReviewGroups.includes(group.group_id)
    && !(authorApprovedReviewJobId===job?.id && authorApprovedReviewGroupIds?.includes(group.group_id))).length;
  const acceptedReviewGroups=authorApprovedReviewJobId===job?.id ? authorApprovedReviewGroupIds?.length || 0 : 0;
  const ensureSaved=()=>{
    requireSaved();
    if(paragraphs.some(p=>hasDraftScratch(`${userId}:${projectId}:${p.paragraph_key}:review-manual`)||hasDraftScratch(`${userId}:${projectId}:${p.paragraph_key}:manual`)))
      throw new Error(text('请先保存或撤销正文修改，再开始修订。','Save or undo manuscript edits before revision.'));
  };
  const submit=useMutation({mutationFn:async()=>{
    ensureSaved();
    return apiRequest(`/api/v1/projects/${encodeURIComponent(projectId)}/draft/review-jobs`,{method:'POST',headers:{'Idempotency-Key':newIdempotencyKey()},
      ...jsonBody({guidance,standard,expected_draft_artifact_id:draftArtifactId})});
  },onSuccess:refresh});
  const runLabel=(run:ReviewRun)=>`${new Date(run.created_at).toLocaleString()} · ${run.status}${run.pending_group_count?` · ${run.pending_group_count} ${text('待确认','pending')}`:''}${run.current?'':text(' · 旧版本',' · historical')}`;
  const acceptCandidate=useMutation({mutationFn:async(group:{group_id:string;candidate_sha256:string})=>{
    ensureSaved();
    if(!job||historical||!current||active||rejectedReviewGroups.includes(group.group_id)||authorApprovedReviewGroupIds?.includes(group.group_id)) throw new Error(text('候选已处理或属于旧稿，不能覆盖当前版本。','The suggestion is already decided or belongs to another draft version.'));
    return apiRequest(`/api/v1/projects/${encodeURIComponent(projectId)}/draft/review-candidates/${encodeURIComponent(job.id)}/${encodeURIComponent(group.group_id)}/accept`,{
      method:'POST',...jsonBody({expected_draft_artifact_id:draftArtifactId,candidate_sha256:group.candidate_sha256})});
  },onSuccess:refresh});
  const rejectCandidate=useMutation({mutationFn:async(group:{group_id:string;candidate_sha256:string})=>{
    if(!job||historical||!current||active||rejectedReviewGroups.includes(group.group_id)||authorApprovedReviewGroupIds?.includes(group.group_id)) throw new Error(text('候选已处理或属于旧稿，不能记录新的决定。','The suggestion is already decided or belongs to another draft version.'));
    return apiRequest(`/api/v1/projects/${encodeURIComponent(projectId)}/draft/review-candidates/${encodeURIComponent(job.id)}/${encodeURIComponent(group.group_id)}/reject`,{
      method:'POST',...jsonBody({expected_draft_artifact_id:draftArtifactId,candidate_sha256:group.candidate_sha256})});
  },onSuccess:refresh});
  const acceptAll=useMutation({mutationFn:async(groups:{group_id:string;candidate_sha256:string}[])=>{
    ensureSaved();
    if(!job||historical||!current||active||!groups.length) throw new Error(text('候选已处理或属于旧稿，不能覆盖当前版本。','The suggestions are already decided or belong to another draft version.'));
    return apiRequest(`/api/v1/projects/${encodeURIComponent(projectId)}/draft/review-candidates/${encodeURIComponent(job.id)}/accept-selected`,{
      method:'POST',...jsonBody({expected_draft_artifact_id:draftArtifactId,groups})});
  },onSuccess:refresh});
  const cancel=useMutation({mutationFn:()=>apiRequest(`/api/v1/jobs/${job!.id}/cancel`,{method:'POST'}),onSuccess:refresh});
  const retry=useMutation({mutationFn:async()=>{ensureSaved();return apiRequest(`/api/v1/jobs/${job!.id}/retry`,{method:'POST',headers:{'Idempotency-Key':newIdempotencyKey()}});},onSuccess:refresh});
  return <section className="peer-review" aria-label={text('AI 审稿式修订','AI review-style revision')}>
    <div className="peer-review-toolbar"><strong>{text('基于证据生成修订候选','Evidence-grounded revision candidates')}</strong>
      {runs.length>1?<label className="peer-review-run-select">{text('审稿报告','Review run')}
        <select value={job?.id||''} disabled={active} onChange={event=>onSelectRun?.(event.target.value)}>
          {runs.map(run=><option key={run.id} value={run.id}>{runLabel(run)}</option>)}
        </select></label>:null}
      <button type="button" className="button button-primary" disabled={blocked||active||submit.isPending||retry.isPending} onClick={()=>{
        if(pendingReviewGroups>0&&!window.confirm(text(`当前还有 ${pendingReviewGroups} 组候选未确认。开始新审稿会切换到新报告；旧候选仍保留在任务记录中，但界面不再显示。是否继续？`,`${pendingReviewGroups} suggestion${pendingReviewGroups===1?'':'s'} ${pendingReviewGroups===1?'still awaits':'still await'} your decision. Starting another review switches this view to the new report; the old candidates remain in the task record but are no longer shown here. Continue?`))) return;
        onSelectRun?.('');
        submit.mutate();
      }}>{text('审稿并生成修改候选','Review and generate revision candidates')}</button></div>
    <p className="peer-review-cost-note">{text('全文审读并生成可对照的修改候选；只有你逐组同意后才写入正文，采纳是人工决定，不代表 AI 已完成独立核验。长稿分组审读，最多2组并行；拆分不保证更便宜。允许收窄或弱化缺乏支持的表述，不猜写事实。模型调用计费。','Reviews the whole article and generates comparable revision candidates. Nothing is written to the manuscript until you accept a group; acceptance is a human decision, not independent AI verification. Long articles use up to 2 parallel planning units; splitting does not guarantee lower cost. Unsupported claims may be narrowed or qualified, never guessed. Model charges apply.')}</p>
    {blocked&&!active&&!historical?<p role="status" className="peer-review-note">{text('当前有未完成的上游同步或其他写作任务。请先完成或停止它，再开始审稿、采纳或拒绝候选；未保存输入仍会保留。','Another upstream sync or writing task is unfinished. Finish or stop it before starting a review or deciding candidates; unsaved input is preserved.')}</p>:null}
    <details><summary>{text('投稿标准与作者要求（可选）','Journal standard and author guidance (optional)')}</summary>
      <label className="peer-review-help-label">{text('目标期刊或文章标准','Target journal or article standard')}<textarea value={standard} maxLength={8000} disabled={active||submit.isPending} onChange={e=>setStandard(e.target.value)}/></label>
      <label className="peer-review-help-label">{text('作者要求','Author guidance')}<textarea value={guidance} maxLength={12000} disabled={active||submit.isPending} onChange={e=>setGuidance(e.target.value)}/></label>
      {failedGuidance||failedStandard?<p role="alert">{text('浏览器无法暂存，请先备份输入。','Browser storage unavailable; back up your input.')}</p>:null}
    </details>
    {retainedInputs.length?<details><summary>{text('旧审稿中保留的作者输入','Retained author input from the old reviewer')}</summary>{retainedInputs.map((input,n)=><label className="peer-review-help-label" key={input.key}>{text(`保留输入 ${n+1}`,`Retained input ${n+1}`)}<textarea readOnly value={input.value}/></label>)}</details>:null}
    {submit.error||retry.error||cancel.error||acceptCandidate.error||rejectCandidate.error||acceptAll.error?<p role="alert"><LocalizedError error={submit.error||retry.error||cancel.error||acceptCandidate.error||rejectCandidate.error||acceptAll.error}/></p>:null}
    {(!job||current)?<PeerReviewProgress job={job} pendingGroupCount={pendingReviewGroups}/>:null}
    {job?<div className="peer-review-run" aria-label={text('本轮执行情况','Run status')}>
      {interrupted?<p role="status">{reviewFailureSummary(job.error_message||'',text)}</p>:active?<p role="status">{job.status==='queued'?text('排队中','Queued'):text('正在生成修改候选，尚未确认的候选不会覆盖正文。','Generating revision candidates; unaccepted candidates do not overwrite the manuscript.')}</p>:current&&job.result?.publication?.changed?<p className="peer-review-saved" role="status">{text('修订稿已保存，原稿保留在版本历史中。','Revised manuscript saved; the original remains in version history.')}</p>:current&&!historical&&job.status==='succeeded'?
        job.result?.outcome==='no_change_needed'?<p role="status">{text('本轮未发现需要修改的内容，原稿保持不变。','No defensible revision was needed; the manuscript is unchanged.')}</p>
        :pendingReviewGroups>0?<p role="status">{text(`已生成 ${pendingReviewGroups} 组待确认候选，原稿尚未改变。`,`${pendingReviewGroups} suggestion${pendingReviewGroups===1?'':'s'} ${pendingReviewGroups===1?'awaits':'await'} your decision; the manuscript is unchanged.`)}</p>
        :acceptedReviewGroups>0?<p className="peer-review-saved" role="status">{text(`已采纳 ${acceptedReviewGroups} 组审稿候选，正文已保存为新版本。`,`Accepted ${acceptedReviewGroups} review group${acceptedReviewGroups===1?'':'s'}; the manuscript was saved as a new version.`)}</p>
        :<p role="status">{text('本轮没有交付可安全采纳的修改；请查看结果及限制说明。','This run did not deliver a safely adoptable revision; read the results and limitations.')}</p>
      :null}
      {current&&job.result?.progress?<p>{text(`已处理 ${job.result.progress.groups_completed} / ${job.result.progress.groups_total} 个修改组`,`Processed ${job.result.progress.groups_completed} / ${job.result.progress.groups_total} revision groups`)}</p>:null}
      {historical?<p className="peer-review-note">{text('这是旧版本稿件的结果，不代表当前正文。','This result belongs to an older manuscript, not the current draft.')}</p>:null}
      {active?<button type="button" className="button button-secondary" disabled={cancel.isPending} onClick={()=>cancel.mutate()}>{text('停止修订','Stop revision')}</button>:null}
      {interrupted&&current&&acceptedReviewGroups===0?<><button type="button" className="button button-secondary" disabled={historical||blocked||retry.isPending} onClick={()=>{onSelectRun?.('');retry.mutate();}}>{text('继续未完成修订','Resume unfinished revision')}</button><small>{text('复用已完成检查点，后续模型调用可能计费。','Reuses completed checkpoints; further model calls may be charged.')}</small></>:null}
      {interrupted&&current&&acceptedReviewGroups>0?<p role="status">{text('已采纳部分候选，不能续跑原审稿任务；剩余候选仍可逐组采纳。','Some candidates were already accepted, so the original review cannot resume; remaining candidates can still be accepted one by one.')}</p>:null}
    </div>:null}
    {paragraphs.filter(p=>hasDraftScratch(`${userId}:${projectId}:${p.paragraph_key}:review-manual`)).map(p=><section key={p.paragraph_key} className="peer-review-legacy-scratch"><h4>{text('保留的未保存编辑','Recovered unsaved edits')}</h4><ParagraphManualEditor projectId={projectId} revision={revision} paragraph={p} refresh={refresh} disabled={active} scratchKey={`${userId}:${projectId}:${p.paragraph_key}:review-manual`} onEditingChange={dirty=>onEditingChange?.(`peer-review:${p.paragraph_key}`,dirty)}/></section>)}
    <PeerReviewReport key={`${userId}:${projectId}`} job={job} extraContent={extraContent}
      acceptedGroupIds={authorApprovedReviewJobId===job?.id?authorApprovedReviewGroupIds:[]}
      rejectedGroupIds={rejectedReviewGroups}
      canAccept={!historical&&!active&&!blocked&&!acceptCandidate.isPending&&!acceptAll.isPending&&!rejectCandidate.isPending}
      canReject={!historical&&!active&&!blocked&&!acceptCandidate.isPending&&!acceptAll.isPending&&!rejectCandidate.isPending}
      onAccept={group=>acceptCandidate.mutate(group)}
      onReject={group=>rejectCandidate.mutate(group)}
      onAcceptAll={groups=>acceptAll.mutate(groups)}/>

  </section>;
}
