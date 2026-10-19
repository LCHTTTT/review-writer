import {useState,type ReactNode} from 'react';
import {MarkdownView} from '../../components/MarkdownView';
import {useUiText} from '../../i18n/useUiText';
import {REVIEW_REVISION_POLICY,type PeerReviewJob} from './peerReviewTypes';
export type ReviewParagraph={paragraph_id:string;paragraph_key:string;text:string;text_sha256:string};

export function PeerReviewReport({job,extraContent}:{job?:PeerReviewJob;extraContent?:ReactNode}) {
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
  return <section className="peer-review-report" aria-label={text('修订结果','Revision results')}>
    {result.summary?<p>{result.summary}</p>:null}
    {groups.length?<>
      <header className="peer-review-window-head"><strong>{text(`修改对照（${groups.length} 组）`,`Revision comparison (${groups.length} groups)`)}</strong></header>
      <div className="peer-review-window">
        <nav className="peer-review-queue" aria-label={text('修改组','Revision groups')}>
          {groups.map((group,n)=><button type="button" key={group.group_id} aria-current={selected?.group_id===group.group_id?'true':undefined} onClick={()=>setSelection(group.group_id)}>
            <span>{n+1}. {group.problem}</span><small>{group.status==='saved'?text('已保存','Saved'):text('候选未保存，不代表已核实','Candidate, not saved or certified')}</small></button>)}
        </nav>
        <article className="peer-review-resolution" aria-label={text('当前修改组','Selected revision group')}>
          <h4>{selected.problem}</h4><p>{selected.explanation}</p>
          {selected.patches.map(patch=><section key={patch.block_id} className="peer-review-repair-preview">
            <h5>{patch.block_id} · {patch.basis==='source'?text('原文依据','Original-source basis'):text('依据正文收窄或调整','Manuscript-grounded revision')}</h5>
            <div className="peer-review-comparison"><section><h5>{text('修改前','Before')}</h5><MarkdownView content={patch.before}/></section><section><h5>{text('修改后','After')}</h5><MarkdownView content={patch.after}/></section></div>
          </section>)}
          {selected.sources.length?<details><summary>{text('查看原文依据','Read original evidence')}</summary>{selected.sources.map(source=><section key={source.ref}><strong>{source.title||source.paper_id}{source.page?` · p.${source.page}`:''}</strong><blockquote>{source.text}</blockquote><small>{source.ref}</small></section>)}</details>:<p className="peer-review-note">{text('本组依据当前正文作结构调整或收窄，不代表独立核实了未修改的科学断言。','This group adjusts or narrows the manuscript; unchanged scientific claims are not independently certified.')}</p>}
        </article>
      </div>
    </>:job.status==='succeeded'&&result.outcome==='no_change_needed'?<p>{text('本轮未发现需要安全修改的内容，未为凑数量改稿。','No defensible revision was needed; no gratuitous changes were made.')}</p>:null}
    {result.limitations?.length?<section className="peer-review-limitations" aria-label={text('未能安全完成的事项','Revision limitations')}><h4>{text('仍需作者决定或补充证据','Author decisions or further evidence needed')}</h4><ul>{result.limitations.map((item,n)=><li key={n}>{item}</li>)}</ul></section>:null}
    {extraContent}
  </section>;
}
