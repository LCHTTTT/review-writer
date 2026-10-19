import {type ReactNode} from 'react';
import {useMutation} from '@tanstack/react-query';
import {apiRequest,jsonBody,newIdempotencyKey} from '../../api/client';
import {LocalizedError} from '../../components/LocalizedError';
import {jobIsActive} from '../../hooks/useJob';
import {useUiText} from '../../i18n/useUiText';
import {ParagraphManualEditor} from './ParagraphManualEditor';
import {PeerReviewReport,type ReviewParagraph} from './PeerReviewReport';
import {PeerReviewProgress,reviewFailureSummary} from './PeerReviewProgress';
import {REVIEW_REVISION_POLICY,type PeerReviewJob} from './peerReviewTypes';
import {DRAFT_SCRATCH_PREFIX,hasDraftScratch,useDraftScratch} from './useDraftScratch';
import './peer-review.css';
export type {PeerReviewJob} from './peerReviewTypes';

type Props={projectId:string;userId:string;revision:number;draftArtifactId:string;sections:{section_id:string;title:string}[];
  paragraphs:ReviewParagraph[];job?:PeerReviewJob;blocked:boolean;requireSaved:()=>void;refresh:()=>Promise<unknown>;
  onEditingChange?:(key:string,dirty:boolean)=>void;extraContent?:ReactNode};
export function DraftPeerReview({projectId,userId,revision,draftArtifactId,paragraphs,job,blocked,requireSaved,refresh,extraContent,onEditingChange}:Props) {
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
  const historical=!!job&&job.source_draft_artifact_id!==draftArtifactId&&job.result?.publication?.draft_artifact_id!==draftArtifactId;
  const interrupted=!!job&&['failed','cancelled','interrupted'].includes(job.status);
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
  const cancel=useMutation({mutationFn:()=>apiRequest(`/api/v1/jobs/${job!.id}/cancel`,{method:'POST'}),onSuccess:refresh});
  const retry=useMutation({mutationFn:async()=>{ensureSaved();return apiRequest(`/api/v1/jobs/${job!.id}/retry`,{method:'POST',headers:{'Idempotency-Key':newIdempotencyKey()}});},onSuccess:refresh});
  return <section className="peer-review" aria-label={text('AI 审稿式修订','AI review-style revision')}>
    <div className="peer-review-toolbar"><strong>{text('基于证据交付修订稿','Evidence-grounded manuscript revision')}</strong>
      <button type="button" className="button button-primary" disabled={blocked||active||submit.isPending||retry.isPending} onClick={()=>submit.mutate()}>{text('审稿并生成修订稿','Review and revise manuscript')}</button></div>
    <p className="peer-review-cost-note">{text('全文审读，成组修改正文、摘要结论、图表和参考文献；独立核验通过后保存。长稿分组审读，最多2组并行后整体核验；拆分不保证更便宜。允许收窄或弱化缺乏支持的表述，不猜写事实。模型调用计费。','Reviews the whole article and coordinates prose, summaries, figures, tables and references. Saves independently verified revisions. Long articles use up to 2 parallel units followed by a whole-article check; splitting does not guarantee lower cost. Unsupported claims may be narrowed or qualified, never guessed. Model charges apply.')}</p>
    <details><summary>{text('投稿标准与作者要求（可选）','Journal standard and author guidance (optional)')}</summary>
      <label className="peer-review-help-label">{text('目标期刊或文章标准','Target journal or article standard')}<textarea value={standard} maxLength={8000} disabled={active||submit.isPending} onChange={e=>setStandard(e.target.value)}/></label>
      <label className="peer-review-help-label">{text('作者要求','Author guidance')}<textarea value={guidance} maxLength={12000} disabled={active||submit.isPending} onChange={e=>setGuidance(e.target.value)}/></label>
      {failedGuidance||failedStandard?<p role="alert">{text('浏览器无法暂存，请先备份输入。','Browser storage unavailable; back up your input.')}</p>:null}
    </details>
    {retainedInputs.length?<details><summary>{text('旧审稿中保留的作者输入','Retained author input from the old reviewer')}</summary>{retainedInputs.map((input,n)=><label className="peer-review-help-label" key={input.key}>{text(`保留输入 ${n+1}`,`Retained input ${n+1}`)}<textarea readOnly value={input.value}/></label>)}</details>:null}
    {submit.error||retry.error||cancel.error?<p role="alert"><LocalizedError error={submit.error||retry.error||cancel.error}/></p>:null}
    {(!job||current)?<PeerReviewProgress job={job}/>:null}
    {job?<div className="peer-review-run" aria-label={text('本轮执行情况','Run status')}>
      {interrupted?<p role="status">{reviewFailureSummary(job.error_message||'',text)}</p>:active?<p role="status">{job.status==='queued'?text('排队中','Queued'):text('正在生成和核验修订稿，尚未保存的候选不会覆盖正文。','Generating and checking the revision; unverified candidates do not overwrite the manuscript.')}</p>:current&&job.result?.publication?.changed?<p className="peer-review-saved" role="status">{text('修订稿已保存，原稿保留在版本历史中。','Revised manuscript saved; the original remains in version history.')}</p>:current&&job.status==='succeeded'?<p role="status">{text('本轮已结束，未保存新修改；请查看结果及限制说明。','Run finished without new saved changes; read the results and limitations.')}</p>:null}
      {current&&job.result?.progress?<p>{text(`已处理 ${job.result.progress.groups_completed} / ${job.result.progress.groups_total} 个修改组`,`Processed ${job.result.progress.groups_completed} / ${job.result.progress.groups_total} revision groups`)}</p>:null}
      {historical?<p className="peer-review-note">{text('这是旧版本稿件的结果，不代表当前正文。','This result belongs to an older manuscript, not the current draft.')}</p>:null}
      {active?<button type="button" className="button button-secondary" disabled={cancel.isPending} onClick={()=>cancel.mutate()}>{text('停止修订','Stop revision')}</button>:null}
      {interrupted&&current?<><button type="button" className="button button-secondary" disabled={historical||blocked||retry.isPending} onClick={()=>retry.mutate()}>{text('继续未完成修订','Resume unfinished revision')}</button><small>{text('复用已完成检查点，后续模型调用可能计费。','Reuses completed checkpoints; further model calls may be charged.')}</small></>:null}
    </div>:null}
    {paragraphs.filter(p=>hasDraftScratch(`${userId}:${projectId}:${p.paragraph_key}:review-manual`)).map(p=><section key={p.paragraph_key} className="peer-review-legacy-scratch"><h4>{text('保留的未保存编辑','Recovered unsaved edits')}</h4><ParagraphManualEditor projectId={projectId} revision={revision} paragraph={p} refresh={refresh} disabled={active} scratchKey={`${userId}:${projectId}:${p.paragraph_key}:review-manual`} onEditingChange={dirty=>onEditingChange?.(`peer-review:${p.paragraph_key}`,dirty)}/></section>)}
    <PeerReviewReport key={`${userId}:${projectId}`} job={job} extraContent={extraContent}/>
  </section>;
}
