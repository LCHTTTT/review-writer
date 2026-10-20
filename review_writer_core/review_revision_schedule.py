"""Deterministic review units; split only at object boundaries, never scientific strings."""
import math
import re

PLAN_THRESHOLD=16000
UNIT_CHARS=10000
MAX_UNITS=16
REVIEW_CONCURRENCY=2


def planning_units(snapshot):
    text=snapshot['text']; blocks=snapshot['blocks']
    if len(text)<=PLAN_THRESHOLD:
        return []
    target=max(UNIT_CHARS,math.ceil(len(text)/MAX_UNITS))
    # Each boundary is before an entire editable object. Gaps (including legends
    # and provenance) belong to the preceding unit, so no text is silently lost.
    boundaries=[0]; start=0; previous=None
    for block in blocks:
        distance=block['start']-start
        coupled=previous and (previous['kind']=='heading' and block['kind']!='heading'
                              or previous['kind']=='table-caption' and block['kind']=='table')
        preferred=block['kind']=='heading' and distance>=target*0.65
        if (preferred or distance>=target and not coupled) and len(boundaries)<MAX_UNITS:
            boundaries.append(block['start']);start=block['start']
        previous=block
    boundaries.append(len(text))
    return [{'unit_id':f'read-{n+1}','start':a,'end':b,
             'object_ids':[x['block_id'] for x in blocks if a<=x['start']<b]}
            for n,(a,b) in enumerate(zip(boundaries,boundaries[1:]))]


def article_overview(snapshot):
    """All headings plus complete framing objects, not guessed body summaries."""
    headings=[]; anchors=[]; framing=False
    for block in snapshot['blocks']:
        if block['kind']=='heading':
            headings.append({'block_id':block['block_id'],'text':block['text']})
            framing=bool(re.search(r'\b(?:abstract|conclusions?|summary)\b|摘要|结论|总结',block['text'],re.I))
        elif framing:
            anchors.append({k:block[k] for k in ('block_id','kind','text')})
    return {'headings':headings,'framing_objects':anchors,
            'object_count':len(snapshot['blocks']),
            'coverage_note':'Body text is reviewed in complete bounded units; this overview is not original-source evidence.'}


def author_pdf_write_scope(snapshot,board,task,checks):
    """Objects the targeted coordinator may inspect/ask to edit; not granted writes."""
    selected={c['paper_id'] for c in checks}
    ids=set(task['read_ids']+task['write_ids'])
    for block in snapshot['blocks']:
        bid=block['block_id']
        if set(board.get('bindings',{}).get(bid,[]))&selected:
            ids.add(bid)
        if block['kind']=='reference' and board.get('citation_map',{}).get(bid.split(':')[1]) in selected:
            ids.add(bid)
    ids.update(b['block_id'] for b in article_overview(snapshot)['framing_objects'])
    return ids


def validate_coordination(value, reviews):
    """Every local suspicion is retained or explicitly limited, not silently lost."""
    concerns={t['concern_id']:t for r in reviews.values() if r.get('status')=='reviewed'
              for t in r['plan']['tasks']}
    if not isinstance(value,dict) or not isinstance(value.get('tasks'),list) or not isinstance(value.get('limitations'),list):
        raise ValueError('全文统筹计划格式无效，局部审读结果保留。')
    handled=[]
    for task in value['tasks']:
        ids=task.get('concern_ids',[]) if isinstance(task,dict) else None
        if not isinstance(ids,list) or any(not isinstance(cid,str) or cid not in concerns for cid in ids):
            raise ValueError('全文统筹引用未知局部审读项，未猜测对应关系。')
        handled.extend(ids)
    unhandled=value.get('unhandled_concerns',[])
    if not isinstance(unhandled,list):
        raise ValueError('全文统筹未完成项格式无效。')
    limits=list(value['limitations'])
    for item in unhandled:
        if (not isinstance(item,dict) or not isinstance(item.get('concern_id'),str)
                or item['concern_id'] not in concerns or not isinstance(item.get('reason'),str) or not item['reason'].strip()):
            raise ValueError('全文统筹未完成项缺少明确原因或对应审读项。')
        cid=item['concern_id'];handled.append(cid)
        limits.append(concerns[cid]['problem']+'：'+item['reason'])
    if len(handled)!=len(set(handled)) or set(handled)!=set(concerns):
        raise ValueError('全文统筹遗漏或重复分配局部审读项；检查点保留，未保存稿件。')
    return {**value,'limitations':limits}


def scopes_conflict(first, second):
    a=set(first['write_ids']);b=set(second['write_ids'])
    return bool(a & (b|set(second['read_ids'])) or b & set(first['read_ids']))
