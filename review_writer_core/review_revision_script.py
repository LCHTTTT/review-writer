"""Native review revision stages, sharing one immutable original-source board."""
import re
import json
from review_writer_core.review_revision_document import digest, plan_object_aliases
from review_writer_core.review_revision import call_json, CATEGORY_LABELS, MAX_GROUPS
from review_writer_core.review_revision_schedule import article_overview
from review_writer_core.review_source_context import contextual_review_evidence, bibliography_context


def prepare_board(project, snapshot, loop):
    rows=loop.matrix_rows(project); metadata=loop.paragraph_metadata(project)
    contract=loop.claim_evidence_contract(project)
    papers={}; bindings={}; hints={}
    for pid,row in sorted(rows.items()):
        doc=loop.load_original_source(project.parent.parent,pid,loop.metadata_record(project.parent.parent,pid))
        blocks=[{'ref':f'{pid}:b{n+1}','paper_id':pid,'page':b.get('page'),'text':str(b.get('text') or '')}
                for n,b in enumerate(doc.get('blocks') or []) if str(b.get('text') or '').strip()]
        papers[pid]={'paper_id':pid,'title':str(row.get('title') or ''),'available':bool(blocks),
                     'source_hash':digest(doc.get('blocks') or []),'blocks':blocks}
    for block in snapshot['blocks']:
        pmeta=metadata.get(block['block_id'].removeprefix('paragraph:'),{})
        bound=list(loop.paragraph_citation_binding(project,{'text':block['text']}).values())
        bound.extend(pmeta.get('cited_paper_ids') or [pmeta.get('paper_id')])
        if block['kind']=='figure':
            anchor=re.search(r'<!--\s*inserted_figure:\s*(\{[^\n]*\})\s*-->\s*$',snapshot['text'][:block['start']])
            if anchor: bound.append(json.loads(anchor.group(1)).get('paper_id'))
        claimed=loop.claim_bound_evidence(block['block_id'].removeprefix('paragraph:'),pmeta,contract) if block['kind']=='paragraph' else {}
        hints[block['block_id']]=[{**passage,'paper_id':pid} for pid,passages in claimed.items() if pid in papers for passage in passages]
        bound.extend(claimed)
        bindings[block['block_id']]=list(dict.fromkeys(p for p in bound if p in papers))
    mapping=re.search(r'<!--\s*citation_map:\s*(\{.*?\})\s*-->',snapshot['text'],re.S)
    citation_map=json.loads(mapping.group(1)) if mapping else {str(e['callout']):e['paper_id'] for e in loop.citation_entries(project)}
    missing=sorted({pid for pids in bindings.values() for pid in pids if not papers[pid]['available']})
    limits=['实际引用的论文 '+pid+' 缺少可用原文，相关科学断言尚不能独立核实；不据此断定论文没有相关结果。' for pid in missing]
    limits.extend('引用编号 ['+str(number)+'] 的论文不在当前项目证据范围，未读取或认证其原文。' for number,pid in citation_map.items() if pid not in papers)
    limits.extend('参考条目 ['+b['block_id'].split(':')[1]+'] 尚未绑定论文原文，不能猜改题名、期刊或 DOI。'
        for b in snapshot['blocks'] if b['kind']=='reference' and b['block_id'].split(':')[1] not in citation_map)
    board={'papers':papers,'bindings':bindings,'manuscript_hash':digest(snapshot['text']),
        'original_hints':hints,'citation_map':citation_map,'claim_constraints':contract.get('claims',{}),
        'paragraph_claim_ids':contract.get('paragraph_claim_ids',{}),
        'limitations':limits}
    board['hash']=digest(board)
    return board


def source_index(board):
    # Planning needs assertion boundaries, not repeated original quotes, fact
    # payloads or past verification reports. Keep full bytes on the frozen board
    # for actual source retrieval and auditing; never truncate scientific text.
    fields=('claim_kind','assertion_ceiling','epistemic_status','support_status',
            'required_for_section','fact_binding_status','review_reasons')
    claims={key:{'allowed_assertion':claim.get('allowed_assertion') or claim.get('claim',''),
                 **{field:claim[field] for field in fields if field in claim}}
            for key,claim in board.get('claim_constraints',{}).items() if isinstance(claim,dict)}
    return {'bindings':board['bindings'],'claim_constraints_navigation_only':claims,
        'paragraph_claim_ids_navigation_only':board.get('paragraph_claim_ids',{}),'coverage_limits':board.get('limitations',[]),'papers':[{'paper_id':p['paper_id'],'title_navigation_only':p['title'],
        'original_available':p['available'],'source_hash':p['source_hash'],'original_blocks':len(p['blocks'])}
        for p in board['papers'].values()]}


def select_sources(task, board, snapshot, loop, feedback=None):
    indexed={b['block_id']:b for b in snapshot['blocks']}
    pids=set(task.get('paper_ids',[]))
    for bid in task['read_ids']:
        pids.update(board['bindings'].get(bid,[]))
    packages=[]
    docs={}
    query=task['problem']+' '+task['strategy']+' '+' '.join(indexed[i]['text'] for i in task['read_ids'])
    if isinstance(feedback,dict): query+=' '+str(feedback.get('reason',''))+' '+str(feedback.get('repair_instruction',''))
    queries=[query,*[indexed[i]['text'] for i in task['read_ids']]]
    for pid in sorted(pids):
        paper=board['papers'][pid]
        docs[pid]={'blocks':[{'text':b['text'],'page':b['page']} for b in paper['blocks']]}
        # Retrieval selects complete blocks; no truncated scientific tokens.
        selected=loop.retrieve_original_passages(pid,query,docs[pid],queries=queries)
        packages.append({'paper_id':pid,'title':paper['title'],'original_passages':selected})
    bibliography={pid for bid in task['read_ids'] if indexed[bid]['kind']=='reference' for pid in board['bindings'].get(bid,[])}
    hints=[hint for bid in task['read_ids'] for hint in board.get('original_hints',{}).get(bid,[]) if hint['paper_id'] in pids]
    expanded=contextual_review_evidence({'evidence':packages},hints,lambda pid:docs.get(pid,{}),bibliography=bibliography)
    sources=[]
    for paper in expanded['evidence']:
        pid=paper['paper_id']; original=board['papers'][pid]
        for passage in paper['original_passages']:
            # Canonical refs always come from the frozen board, never retrieval aliases.
            matches=[b for b in original['blocks'] if ' '.join(b['text'].split())==' '.join(passage['text'].split())]
            if len(matches)==1:
                sources.append({**matches[0],'source_hash':original['source_hash'],'title':original['title']})
    return list({s['ref']:s for s in sources}.values())


def execute(project, request, loop):
    stage=request['stage']; snapshot=request['snapshot']; board=request.get('board')
    if stage=='prepare':
        return {'board':prepare_board(project,snapshot,loop)}
    if stage=='validate_sources':
        current=prepare_board(project,snapshot,loop)
        return {'unchanged':current['hash']==board['hash'],'board_hash':current['hash']}
    model_call=loop.call_json_model if 'model_response' not in request else lambda *a,**kw:request['model_response']
    if stage=='plan_slice':
        unit=request['unit'];a=unit['start'];b=unit['end']
        blocks=[{**x,'start':x['start']-a,'end':x['end']-a} for x in snapshot['blocks'] if x['block_id'] in unit['object_ids']]
        view={**snapshot,'text':snapshot['text'][a:b],'blocks':blocks}
        pids={pid for bid in unit['object_ids'] for pid in board['bindings'].get(bid,[])}
        claim_ids={cid for bid in unit['object_ids'] for key in (bid,bid.removeprefix('paragraph:'))
                   for cid in board.get('paragraph_claim_ids',{}).get(key,[])}
        navigation={**board,'papers':{pid:p for pid,p in board['papers'].items() if pid in pids},
            'bindings':{bid:board['bindings'].get(bid,[]) for bid in unit['object_ids']},
            'claim_constraints':{cid:c for cid,c in board.get('claim_constraints',{}).items() if cid in claim_ids},
            'paragraph_claim_ids':{bid:ids for bid,ids in board.get('paragraph_claim_ids',{}).items() if bid in unit['object_ids'] or 'paragraph:'+bid in unit['object_ids']}}
        return execute(project,{**request,'stage':'plan','snapshot':view,'board':navigation,
            'overview':article_overview(snapshot),'label_suffix':unit['unit_id']},loop)
    common={'standard':request.get('standard',''),'guidance_unverified':request.get('guidance',''),
            'evidence_board_hash':board['hash'],'citation_map':board.get('citation_map',{})}
    if stage in ('propose','audit'):
        common['actual_bibliography']=bibliography_context(snapshot['text'])
    if stage=='plan':
        return call_json(model_call,'plan'+(' '+request['label_suffix'] if request.get('label_suffix') else ''),{**common,'manuscript':snapshot['text'],
            **({'article_overview':request['overview']} if request.get('overview') else {}),
            'objects':[{key:b[key] for key in ('block_id','kind','start','end','sha256')} for b in snapshot['blocks']],
            'object_aliases':plan_object_aliases(snapshot['text'],snapshot['blocks']),
            'evidence_index':source_index(board)},
            ('Read this COMPLETE assigned unit in its article overview; plan local changes only to supplied objects. '
             'Report cross-unit concerns for coordination; do not infer missing body coverage from this slice. '
             if request.get('overview') else 'Read the ENTIRE manuscript. ')
            +'Object start/end are Python character offsets into manuscript (end exclusive); '
            'the manuscript is supplied once, without duplicating object text. '
            'Use ONLY objects[].block_id in read_ids/write_ids, not paragraph_id comments or claim IDs; '
            'object_aliases resolves legacy generated-table markers to the actual table block_id, NOT its caption or legend. '
            'Return {summary,tasks:[{category,problem,strategy,write_ids:[],read_ids:[],paper_ids:[]}],limitations:[]}. '
            'category MUST be one exact short code from this mapping (do NOT output the descriptive labels): '
            +json.dumps(CATEGORY_LABELS)+'. '
            f'Create at most {MAX_GROUPS} substantive root-cause tasks, each with a concrete achievable revision strategy. '
            'Keep problem and strategy each to 1-3 concise sentences; return no replacement text, copied source passages or extra fields at this stage. '
            'Combine all edits to the same object into ONE task; coordinate abstract/body/conclusion where needed. '
            'read_ids must include every actual supporting/comparison object. All write_ids must be included in read_ids. '
            'Do not yet claim findings verified. Choose relevant existing papers from the index for original checks. '
            'Missing sources or major author decisions go to explicit limitations; do not invent an executable fix.')
    if stage=='coordinate':
        return call_json(model_call,'coordinate',{**common,'article_overview':article_overview(snapshot),
            'objects':[{k:b[k] for k in ('block_id','kind')} for b in snapshot['blocks']],
            'unit_reviews':request['unit_reviews'],'coverage_limits':board.get('limitations',[])},
            'Coordinate the complete set of unit reviews into ONE bounded revision plan. '
            'The overview includes full abstract/conclusion and all headings, not the full body. '
            'Preserve every important local concern, merge shared writable objects and coordinate abstract/body/conclusion, '
            'tables/captions/discussion and cross-study comparisons. Do not treat navigation or suspected findings as scientific proof. '
            'Failed/unreviewed units remain explicit coverage limitations; do not invent successful coverage. '
            'Do not silently discard concerns to fit the limit; merge related tasks or disclose unhandled concerns as limitations. '
            'Return {summary,tasks:[{category,problem,strategy,write_ids:[],read_ids:[],paper_ids:[],concern_ids:[]}],'
            'limitations:[],unhandled_concerns:[{concern_id,reason}]}. '
            'Every supplied concern_id MUST occur exactly once, either in a task concern_ids or in unhandled_concerns '
            'with a concrete reason; do not silently omit a local concern. New cross-unit tasks may have empty concern_ids. '
            f'At most {MAX_GROUPS} atomic root-cause tasks; category MUST use these codes: '+json.dumps(CATEGORY_LABELS)+'. '
            'Use supplied object IDs only. Keep explanations concise; no replacement text or copied evidence.')
    if stage=='propose':
        task=request['task']; sources=select_sources(task,board,snapshot,loop,request.get('feedback'))
        rows={b['block_id']:b for b in snapshot['blocks']}
        value=call_json(model_call,'propose',{**common,'task':task,
            'objects':[rows[bid] for bid in task['read_ids']],'original_sources':sources,
            'feedback':request.get('feedback'), 'previous_candidate':request.get('previous_candidate')},
            'Independently test the proposed diagnosis; do not implement a false finding. Return '
            '{explanation,disposition:"revised"|"not_needed"|"blocked",patches:[{block_id,before_sha256,after,basis:"source"|"manuscript",'
            'operation:"correct"|"narrow"|"qualify"|"editorial",source_refs:[],support_ids:[]}],limitation}. '
            'Deliver complete replacement text for each affected object, not advice. Only write write_ids. '
            'For science corrections use actual source refs. Manuscript-only edits may narrow/qualify/organize '
            'with explicit support_ids but cannot invent scientific facts, new numerical values or pretend original verification. '
            'For figures return image and caption with URLs/numbering/rights unchanged; fix caption/alt only. '
            'For tables preserve valid Markdown and supported rows; for bibliography preserve the bound reference number. '
            'For a false or unnecessary concern return disposition:"not_needed", patches:[] and explain the actual evidence; '
            'this receives independent verification and must not become author busywork. For genuine unfixable concerns '
            'return disposition:"blocked", patches:[] and an honest limitation.')
        return {'proposal':value,'sources':sources}
    if stage=='audit':
        group=request['group']; rows={b['block_id']:b for b in snapshot['blocks']}
        return call_json(model_call,'audit',{**common,'task':request['task'],'candidate_group':group,
            'original_objects':[rows[i] for i in group['read_ids']]},
            'Independently check EVERY semantic change, removals, implications and every affected object. '
            'Verify the ORIGINAL problem actually disappears, the diagnosis is valid, source/statement attribution '
            'is correct, supported core findings/limitations are preserved, and no direct regressions, scope expansion '
            'or unsupported new facts are introduced. Do not treat the proposal explanation as proof. '
            'Unchanged baseline claims are not newly verified; lack of unrelated sources alone is not a reason to reject '
            'a narrowly supported change. Manuscript-only narrowing can be checked against supplied objects, not certified as science. '
            'Return {status:"pass"|"revise"|"blocked",problem_resolved:boolean,preserves_core_information:boolean,'
            'no_unsupported_claims:boolean,no_scope_expansion:boolean,no_direct_regressions:boolean,'
            'checked_patch_ids:[],source_refs:[],reason,repair_instruction}. Include every checked patch and actual source ref. '
            'For a not_needed candidate independently establish that the concern is false or no change is warranted: '
            'add no_change_needed:true and checked_object_ids covering every supplied original object; '
            'include actual source_refs when sources were supplied. Do not dismiss a real important defect. '
            'If unsupported explain a concrete bounded correction, or blocked if missing evidence/author decision prevents it.')
    if stage=='final':
        return call_json(model_call,'final',{**common,'original_manuscript':snapshot['text'],
            'revised_manuscript':request['revised_text'],'groups':request['groups']},
            'Check the EXACT retained combination as a whole review article: theme, structure, abstract/body/conclusion, '
            'comparison boundaries, figure/table callouts and reference consistency. Check changes and their direct consequences, '
            'not a new open-ended fault hunt. Verify no core information is lost or scientific scope enlarged. '
            'Return {status:"pass"|"blocked",consistent:boolean,preserves_core_information:boolean,'
            'checked_group_ids:[],affected_group_ids:[],reason}. All supplied groups must be explicitly checked. '
            'If blocked identify the affected groups, or all groups when the cause cannot be isolated.')
    raise ValueError('Unknown review revision stage')
