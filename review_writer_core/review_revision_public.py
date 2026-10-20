"""Small read projection; immutable evidence/checkpoints remain in the owned job."""
from review_writer_core.review_revision import POLICY, audit_passed
from review_writer_core.review_revision_document import digest, document_blocks
from review_writer_core.chemical_typography import normalize_chemical_typography


def review_result(result, payload=None, *, raw_source_text=None):
    value=result or {}
    if value.get('policy')!=POLICY and (payload or {}).get('flow_policy')!=POLICY:
        # Historical reports are read-only, never converted into new revision tasks.
        return {k:value[k] for k in ('issues','review_diagnostics','publication','phase','review_status') if k in value}
    public={'policy':POLICY, **{k:value[k] for k in ('phase','progress','publication','outcome','summary','limitations') if k in value}}
    indexed={b['block_id']:b for b in (payload or {}).get('snapshot',{}).get('blocks',[])}
    raw_blocks={b['block_id']:b for b in document_blocks(raw_source_text)} if isinstance(raw_source_text,str) else {}
    revisions=list(value.get('revisions') or [])
    shown={g['group_id'] for g in revisions}
    for gid,record in value.get('groups',{}).items():
        candidate=record.get('candidate') if (record.get('candidate') or {}).get('patches') else next(
            (a['candidate'] for a in reversed(record.get('attempts',[])) if (a.get('candidate') or {}).get('patches')),None)
        if gid not in shown and candidate:
            revisions.append({**candidate,'status':'not_saved'})
    public['revisions']=[]
    for group in revisions:
        refs={ref for patch in group['patches'] for ref in patch['source_refs']}
        gid=group['group_id'];record=value.get('groups',{}).get(gid,{})
        original=record.get('candidate') if (record.get('candidate') or {}).get('patches') else next(
            (a['candidate'] for a in reversed(record.get('attempts',[])) if (a.get('candidate') or {}).get('patches')),None)
        audit=record.get('audit')
        public['revisions'].append({k:group[k] for k in ('group_id','category','problem','explanation','status') if k in group} | {
            'candidate_sha256':digest(original) if original and original.get('patches') else '',
            'audit_failed':bool(audit and original and not audit_passed(audit,original)),
            'patches':[{**p,'before':(raw_blocks[p['block_id']]['text']
                if p['block_id'] in raw_blocks and p['block_id'] in indexed
                    and normalize_chemical_typography(raw_blocks[p['block_id']]['text'])==indexed[p['block_id']]['text']
                else indexed.get(p['block_id'],{}).get('text',''))} for p in group['patches']],
            'sources':[s for s in group.get('sources',[]) if s['ref'] in refs]})
    displayed={g['group_id'] for g in public['revisions']}
    for task in value.get('plan',{}).get('tasks',[]):
        if task['group_id'] not in displayed:
            record=value.get('groups',{}).get(task['group_id'],{})
            dismissed=record.get('status')=='dismissed'
            reason=(record.get('reason') or (record.get('candidate') or {}).get('explanation'))
            public['revisions'].append({'group_id':task['group_id'],'category':task['category'],
                'problem':task['problem'],'explanation':reason or ('经复核无需修改。' if dismissed
                    else 'AI 未交付具体修改文字，不能将问题描述当作候选。'),
                'status':'dismissed' if dismissed else 'no_candidate','candidate_sha256':'',
                'patches':[],'sources':[]})
    order={task['group_id']:n for n,task in enumerate(value.get('plan',{}).get('tasks',[]))}
    public['revisions'].sort(key=lambda row:order.get(row['group_id'],len(order)))
    return public


def pending_review_group_count(public, accepted_group_ids=(), rejected_group_ids=()):
    """Count concrete candidates the author has not accepted or rejected yet."""
    decided=set(accepted_group_ids or ()) | set(rejected_group_ids or ())
    return sum(1 for group in (public or {}).get('revisions') or []
        if group.get('status')=='not_saved' and group.get('patches')
        and group.get('group_id') not in decided)
