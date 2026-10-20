import { useUiText } from '../../i18n/useUiText';
import { jobIsActive } from '../../hooks/useJob';
import type { PeerReviewJob } from './peerReviewTypes';

export function reviewFailureSummary(message:string, text:(zh:string,en:string)=>string) {
  return message || text('修订未完成，原稿和已生成候选保留。','Revision did not finish. The original and generated candidates are preserved.');
}
export function PeerReviewProgress({job,pendingGroupCount=0}:{job?:PeerReviewJob;pendingGroupCount?:number}) {
  const {text}=useUiText(); const phase=job?.result?.phase || '';
  const current=phase==='prepare'?0:phase==='plan'?1:['propose','audit'].includes(phase)?2:['final','validate_sources','publication','completed'].includes(phase)?3:-1;
  const active=jobIsActive(job?.status); const progress=job?.result?.progress;
  const awaiting=phase==='completed'&&job?.result?.outcome==='awaiting_author_choice';
  const unitProgress=phase==='plan'&&!!progress?.planning_total
    ?text(`已处理 ${progress.planning_processed||0}/${progress.planning_total} 组，未完成 ${progress.planning_failed||0} 组`,`Processed ${progress.planning_processed||0}/${progress.planning_total} units; ${progress.planning_failed||0} incomplete`):'';
  const steps=[text('证据底稿','Evidence board'),text('全文审读','Manuscript review'),text('成组修订','Grouped revision'),text('作者确认','Author decision')];
  return <><ol className="peer-review-steps revision" aria-label={text('修订进度','Revision progress')}>
    {steps.map((label,n)=>{
      const limited=phase==='completed'&&['limited','revised_with_limits'].includes(job?.result?.outcome||'');
      const status=phase==='completed'?(awaiting
        ?(n===3?(pendingGroupCount>0?'incomplete':'complete'):'complete')
        :(limited&&(n===3||n===2&&!job?.result?.publication?.changed)?'incomplete':'complete')):n<current?'complete':n===current?active?'running':'incomplete':'pending';
      const incompleteLabel=phase==='completed'?(awaiting?text('待作者确认','Awaiting your decision'):text('未全部完成','Partial')):text('已暂停','Paused');
      return <li key={label} className={`peer-review-step ${status}`}><span className="peer-review-step-mark">{n+1}</span><span><strong>{label}</strong><small>{status==='complete'?text('已完成','Complete'):status==='running'?text('进行中','Running'):status==='incomplete'?incompleteLabel:text('待开始','Pending')}</small></span></li>;
    })}
  </ol>{unitProgress?<p role="status" className="peer-review-cost-note">{unitProgress}{active?text('；最多2组并行，已完成结果会保留。','; up to 2 units in parallel; completed results are retained.'):''}</p>:null}</>;
}
