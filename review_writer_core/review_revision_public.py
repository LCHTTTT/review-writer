"""Small read projection; immutable evidence/checkpoints remain in the owned job."""
from review_writer_core.review_revision import POLICY


def review_result(result, payload=None):
    value=result or {}
    if value.get('policy')!=POLICY and (payload or {}).get('flow_policy')!=POLICY:
        # Historical reports are read-only, never converted into new revision tasks.
        return {k:value[k] for k in ('issues','review_diagnostics','publication','phase','review_status') if k in value}
    public={'policy':POLICY, **{k:value[k] for k in ('phase','progress','publication','outcome','summary','limitations') if k in value}}
    indexed={b['block_id']:b for b in (payload or {}).get('snapshot',{}).get('blocks',[])}
    revisions=list(value.get('revisions') or [])
    shown={g['group_id'] for g in revisions}
    for gid,record in value.get('groups',{}).items():
        candidate=record.get('candidate') or next((a['candidate'] for a in reversed(record.get('attempts',[])) if a.get('candidate')),None)
        if gid not in shown and candidate and candidate.get('patches'):
            revisions.append({**candidate,'status':'not_saved'})
    public['revisions']=[]
    for group in revisions:
        refs={ref for patch in group['patches'] for ref in patch['source_refs']}
        public['revisions'].append({k:group[k] for k in ('group_id','category','problem','explanation','status') if k in group} | {
            'patches':[{**p,'before':indexed.get(p['block_id'],{}).get('text','')} for p in group['patches']],
            'sources':[s for s in group.get('sources',[]) if s['ref'] in refs]})
    return public
