import {useState,type ReactNode} from 'react';
import {MarkdownView} from '../../components/MarkdownView';
import {useUiText} from '../../i18n/useUiText';
import {REVIEW_REVISION_POLICY,type PeerReviewJob} from './peerReviewTypes';
export type ReviewParagraph={paragraph_id:string;paragraph_key:string;text:string;text_sha256:string};

type CandidateChoice={group_id:string;candidate_sha256:string};

export function PeerReviewReport({job,extraContent,canAccept=false,onAccept,acceptedGroupIds=[],rejectedGroupIds=[],canReject=false,onReject,onAcceptAll}:{
  job?:PeerReviewJob;extraContent?:ReactNode;canAccept?:boolean;acceptedGroupIds?:string[];onAccept?:(group:CandidateChoice)=>void;
  rejectedGroupIds?:string[];canReject?:boolean;onReject?:(group:CandidateChoice)=>void;onAcceptAll?:(groups:CandidateChoice[])=>void}) {
  const {text}=useUiText(); const [selection,setSelection]=useState(''); const result=job?.result;
  if (!job) return extraContent || null;
  if (result?.policy!==REVIEW_REVISION_POLICY) return <section className="peer-review-report">
    <h4>{text('历史审稿报告（只读）','Historical review (read-only)')}</h4>
    <p>{text('旧审稿逻辑已停用。原报告保留，启动新修订不会重放旧任务。','The old reviewer is retired. The report is preserved; a new revision will not replay the old task.')}</p>
    <details><summary>{text('查看原报告','Read original report')}</summary>
      <ul>{result?.issues?.map((item,n)=><li key={item.issue_id||n}>{item.diagnosis}</li>)}</ul>
      {result?.review_diagnostics?.map((item,n)=><p key={n}>{item.diagnosis || item.message}</p>)}
    </details>{extraContent}</section>;
  const groups=result.revisions || []; const selected=groups.find(g=>g.group_id===selection)||groups[0];
  const pending=groups.filter(group=>group.status==='not_saved'&&group.patches.length>0&&group.candidate_sha256&&!group.audit_failed
    &&!acceptedGroupIds.includes(group.group_id)&&!rejectedGroupIds.includes(group.group_id));
  const selectedRejected=!!selected&&rejectedGroupIds.includes(selected.group_id);
  return <section className="peer-review-report" aria-label={text('修订结果','Revision results')}>
    {result.summary?<p>{result.summary}</p>:null}
    {groups.length?<>
      <header className="peer-review-window-head"><strong>{text(`修改对照（${groups.length} 组）`,`Revision comparison (${groups.length} groups)`)}</strong>
        {pending.length>1?<button type="button" className="button button-secondary" disabled={!canAccept} onClick={()=>onAcceptAll?.(pending.map(group=>({group_id:group.group_id,candidate_sha256:group.candidate_sha256!})))}>{text(`采纳全部待确认（${pending.length}）`,`Accept all pending (${pending.length})`)}</button>:null}
      </header>
      {pending.length>1?<p className="peer-review-note">{text('批量采纳会把所选候选保存为同一个新版本；若组间冲突，请逐组同意。','Accepting all saves the selected candidates as one new version; if groups conflict, accept them one by one.')}</p>:null}
      <div className="peer-review-window">
        <nav className="peer-review-queue" aria-label={text('修改组','Revision groups')}>
          {groups.map((group,n)=><button type="button" key={group.group_id} aria-current={selected?.group_id===group.group_id?'true':undefined} onClick={()=>setSelection(group.group_id)}>
            <span>{n+1}. {group.problem}</span><small>{acceptedGroupIds.includes(group.group_id)?text('已同意并保存','Accepted and saved'):rejectedGroupIds.includes(group.group_id)?text('不采纳，已保留原文','Rejected; original kept'):group.status==='saved'?text('已保存','Saved'):group.status==='dismissed'?text('复核后无需修改','No change needed after review'):group.patches.length?text('AI 候选，待你决定','AI suggestion, awaiting your decision'):text('暂无具体候选','No concrete suggestion')}</small></button>)}
        </nav>
        <article className="peer-review-resolution" aria-label={text('当前修改组','Selected revision group')}>
          <h4>{selected.problem}</h4><p>{selected.explanation}</p>
          {selected.audit_failed?<p role="status" className="message message-warning">{text('历史报告中的独立核验未通过；本组不进入批量采纳，请逐条判断后再同意。','The historical independent audit did not pass. This group is excluded from batch acceptance; review it before accepting individually.')}</p>:null}
          {selectedRejected?<p role="status">{text('本组已标记为不采纳，正文未改变。','This group was rejected; the manuscript is unchanged.')}</p>:selected.status==='dismissed'?<p role="status">{text('经复核，本组疑点不需要修改正文。','No manuscript change was needed for this group.')}</p>:!selected.patches.length?<p role="status">{text('本组没有修改后文字，不能直接同意。','No proposed replacement text for this group; there is nothing to accept.')}</p>:null}
          {selected.patches.map(patch=><section key={patch.block_id} className="peer-review-repair-preview">
            <h5>{patch.block_id} · {selected.status==='not_saved'?text('AI 提议，尚未经核实','AI suggestion, not independently verified'):patch.basis==='source'?text('解析原文依据','Parsed original-source basis'):patch.basis==='author_pdf'?text('作者核对原版 PDF 的转录；AI 未目视 PDF','Author-transcribed PDF; AI did not view the page'):text('依据正文收窄或调整','Manuscript-grounded revision')}</h5>
            <div className="peer-review-comparison"><section><h5>{text('修改前','Before')}</h5><MarkdownView content={patch.before}/></section><section><h5>{text('修改后','After')}</h5><MarkdownView content={patch.after}/></section></div>
          </section>)}
          {selected.status==='not_saved'&&selected.patches.length&&selected.candidate_sha256&&!acceptedGroupIds.includes(selected.group_id)&&!selectedRejected?<><button type="button" className="button button-primary"
            disabled={!canAccept} onClick={()=>onAccept?.({group_id:selected.group_id,candidate_sha256:selected.candidate_sha256!})}>
            {text('同意修改','Accept this revision')}</button>
            <button type="button" className="button button-secondary" disabled={!canReject} onClick={()=>onReject?.({group_id:selected.group_id,candidate_sha256:selected.candidate_sha256!})}>
              {text('不采纳，保留原文','Reject; keep original')}</button>
            <p className="peer-review-note">{text('同意会按你看到的文字保存为人工修订；不采纳只记录决定，不改变正文。两者都不代表 AI 已科学核实，也不要求再次调用模型。',
              'Accepting saves the exact text you approve as a human revision; rejecting only records the decision and leaves the manuscript unchanged. Neither is AI scientific certification, and neither makes a model call.')}</p></>:null}
          {selected.sources.length?<details><summary>{selected.status==='not_saved'?text('查看 AI 使用的原文线索（未经核实）','Read AI source hints (unverified)'):text('查看证据及原版 PDF','Read evidence and original PDF')}</summary>{selected.sources.map(source=><section key={source.ref}><strong>{source.title||source.paper_id}{source.page?` · p.${source.page}`:''}</strong>{source.origin==='author_inspected_original_pdf'?<p className="peer-review-note">{text('用户对照原版 PDF 确认的引文（可能由 AI 先建议）；AI 未目视核对该页。','Quote confirmed by the user against the original PDF (possibly first suggested by AI); AI did not visually verify the page.')}</p>:null}<blockquote>{source.text}</blockquote><small>{source.ref}</small> <a href={`/api/v1/library/papers/${encodeURIComponent(source.paper_id)}/pdf${source.page?`#page=${source.page}`:''}`} target="_blank" rel="noopener noreferrer">{text('打开原版 PDF','Open original PDF')}</a></section>)}</details>:selected.patches.length?<p className="peer-review-note">{text('本组未提供可独立核实的原文线索；修改仍需你自行判断，不代表科学事实已被核实。','No independently verified source hint is available; judge the suggestion yourself. Scientific claims are not certified.')}</p>:null}
        </article>
      </div>
    </>:job.status==='succeeded'&&result.outcome==='no_change_needed'?<p>{text('本轮未发现需要安全修改的内容，未为凑数量改稿。','No defensible revision was needed; no gratuitous changes were made.')}</p>:null}
    {result.limitations?.length?<section className="peer-review-limitations" aria-label={text('未能安全完成的事项','Revision limitations')}><h4>{text('未完成的修订及原因（不一定需要作者处理）','Unfinished revisions and reasons (not always author action)')}</h4><ul>{result.limitations.map((item,n)=><li key={n}>{item}</li>)}</ul></section>:null}
    {extraContent}
  </section>;
}
