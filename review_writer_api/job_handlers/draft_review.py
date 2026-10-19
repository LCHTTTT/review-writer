"""Review-as-revision executor. Retired review/finding/repair loops are not run."""
from copy import deepcopy
from review_writer_api.domain_services.draft_review import validate_review, publish_review
from review_writer_api.errors import WorkflowValidationError, WorkflowConflict
from review_writer_api.job_service import JobYieldRequested
from review_writer_api.scientific_runner import ScientificModelDeferred
from review_writer_api.job_handlers.stage_execution import wait_for_delegated_model, yield_to_delegated_model
from review_writer_api.job_handlers.draft_review_calls import ReviewCalls, ReviewPending, ReviewUnitFailed
from review_writer_core.review_revision_schedule import planning_units, validate_coordination
from review_writer_api.security import Principal, Role
from review_writer_core.review_revision import (POLICY, MAX_ATTEMPTS, validate_plan, validate_group,
    audit_passed, final_passed, group_check_fingerprint)
from review_writer_core.review_revision_document import digest, apply_groups
from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT


def run_review(service, builder, context, payload, *, planning=None):
    if payload.get('flow_policy')!=POLICY:
        raise WorkflowValidationError('旧审稿逻辑已删除。历史报告保留，请从当前已保存初稿启动审稿式修订；不会重放旧模型请求。')
    principal=Principal(context.user_id,frozenset({Role.USER}))
    job=context.repository.get_job(context.user_id,context.job_id)
    state=deepcopy(job.result or {})
    if not state and job.retry_of_job_id:
        prior=context.repository.get_job(context.user_id,job.retry_of_job_id)
        if prior and prior.project_id==context.project_id and prior.payload.get('flow_policy')==POLICY:
            state=deepcopy(prior.result or {})
    if state and state.get('policy')!=POLICY:
        raise WorkflowValidationError('检查点不属于当前审稿式修订版本，未转换或重发。')
    state.setdefault('policy',POLICY); state.setdefault('groups',{}); state.setdefault('limitations',[])
    _,artifact=service._read_text(principal,context.project_id,DRAFT_MANUSCRIPT)
    known_jobs={context.job_id,job.retry_of_job_id}-{None}
    def committed(row):
        return (row.metadata.get('peer_review_job_id') in known_jobs
            and row.metadata.get('review_revision_policy')==POLICY
            and row.metadata.get('previous_draft_artifact_id')==payload['source_draft_artifact_id']
            and row.metadata.get('review_revision_text_sha256')==row.content_sha256)
    delivered=artifact if committed(artifact) else None
    if not delivered and state.get('phase') in ('publication','completed'):
        delivered=next((row for row in service.repository.list_artifacts(context.user_id,context.project_id,DRAFT_MANUSCRIPT) if committed(row)),None)
    if delivered:
        artifact=delivered
        saved_ids={g['group_id'] for g in artifact.metadata.get('review_revision_groups',[])}
        state['revisions']=[{**g['candidate'],'status':'saved'} for gid,g in state['groups'].items() if gid in saved_ids and g.get('candidate')]
        state.update(phase='completed',publication={'changed':True,'draft_artifact_id':artifact.id,
            'saved_groups':len(saved_ids),'saved_objects':sum(len(g['patches']) for g in state['revisions'])},
            summary=state.get('plan',{}).get('summary',''),outcome='revised_with_limits' if state['limitations'] else 'revised')
        context.report_partial_result(state)
        return state
    validate_review(service,principal,context.project_id,payload)
    if 'model_calls' not in state:
        wait_for_delegated_model(context,job.result)
    state.pop('waiting_model_job_ids',None); state.pop('waiting_model_job_id',None)
    snapshot=payload['snapshot']
    def checkpoint(phase):
        state['phase']='plan' if phase in ('plan_slice','coordinate') else phase
        pending=[r['job_id'] for r in state.get('model_calls',{}).values() if r.get('status')=='waiting']
        state['waiting_model_job_ids']=pending
        tasks=state.get('plan',{}).get('tasks',[])
        done=sum(g.get('status') in ('verified','limited','dismissed') for g in state['groups'].values())
        state['progress']={'groups_completed':done,'groups_total':len(tasks),
            'verified_groups':sum(g.get('status')=='verified' for g in state['groups'].values()),
            'planning_completed':sum(r.get('status')=='reviewed' for r in state.get('unit_reviews',{}).values()),
            'planning_processed':len(state.get('unit_reviews',{})),
            'planning_failed':sum(r.get('status')=='limited' for r in state.get('unit_reviews',{}).values()),
            'planning_total':len(state.get('planning_units',[])), 'active_model_calls':len(pending)}
        context.report_partial_result(state); context.report_progress(done,len(tasks)); context.checkpoint()
    def invoke(stage, **extra):
        if stage not in ('prepare','validate_sources'):
            return calls.invoke(stage,**extra)
        checkpoint(stage)
        request={'stage':stage,'snapshot':snapshot,'board':state.get('board'),
            'standard':payload.get('standard',''),'guidance':payload.get('guidance',''),**extra}
        one={**payload,'revision_mode':'dialogue','dialogue':{'review_revision':request}}
        try:
            value=builder(context,one)
        except ScientificModelDeferred as exc:
            yield_to_delegated_model(context,exc)
        if not isinstance(value,dict):
            raise WorkflowValidationError('修订阶段未返回有效结果，检查点保留，未覆盖正文。')
        return value
    calls=ReviewCalls(context,state,payload,builder,checkpoint)
    if 'board' not in state:
        board=invoke('prepare').get('board')
        if not isinstance(board,dict) or board.get('manuscript_hash')!=snapshot['sha256'] or board.get('hash')!=digest({k:v for k,v in board.items() if k!='hash'}):
            raise WorkflowValidationError('证据底稿与初稿快照不一致。')
        state['board']=board; checkpoint('prepare'); raise JobYieldRequested()
    board=state['board']
    if 'plan_raw' not in state:
        units=state.setdefault('planning_units',planning_units(snapshot))
        if units:
            reviews=state.setdefault('unit_reviews',{})
            for unit in units:
                uid=unit['unit_id']
                if uid in reviews: continue
                try:
                    raw=invoke('plan_slice',call_key=uid,unit=unit)
                    local=validate_plan(raw,snapshot['blocks'],board,markdown=snapshot['text'])
                    if any(set(t['write_ids'])-set(unit['object_ids']) for t in local['tasks']):
                        raise ValueError('分组审读计划超出本单元可写对象范围。')
                    for n,task in enumerate(local['tasks'],1): task['concern_id']=f'{uid}:{n}'
                    reviews[uid]={'status':'reviewed','plan':local}
                except ReviewPending:
                    continue
                except (ReviewUnitFailed,ValueError,TypeError,KeyError) as exc:
                    message=uid+' 未完成审读：'+str(exc)+'；该部分不视为已核实。'
                    reviews[uid]={'status':'limited','limitation':message}
                    state['limitations'].append(message)
                checkpoint('plan'); raise JobYieldRequested()
            if len(reviews)<len(units): calls.wait()
            if not any(r['status']=='reviewed' for r in reviews.values()):
                state['plan_raw']={'summary':'分组审读未取得有效结果，原稿保持不变。','tasks':[],
                                   'limitations':[r['limitation'] for r in reviews.values()]}
            else:
                try:
                    state['plan_raw']=validate_coordination(invoke('coordinate',unit_reviews=reviews),reviews)
                except ReviewPending: calls.wait()
                except (ValueError,TypeError,KeyError) as exc:
                    raise WorkflowValidationError(str(exc)) from exc
        else:
            try: state['plan_raw']=invoke('plan')
            except ReviewPending: calls.wait()
        checkpoint('plan'); raise JobYieldRequested()
    if 'plan' not in state:
        try:
            state['plan']=validate_plan(state['plan_raw'],snapshot['blocks'],board,markdown=snapshot['text'])
        except (ValueError,TypeError,KeyError) as exc:
            raise WorkflowValidationError(str(exc)) from exc
        state['limitations']=list(dict.fromkeys([*state['limitations'],*board.get('limitations',[]),*state['plan']['limitations']]))
        checkpoint('plan')
    def limit(task, reason):
        entry=state['groups'][task['group_id']]
        entry.update(status='limited',reason=reason)
        message=task['problem']+'：'+reason
        if message not in state['limitations']:
            state['limitations'].append(message)
    for task in state['plan']['tasks']:
        gid=task['group_id']; entry=state['groups'].setdefault(gid,{'attempts':[]})
        if entry.get('status') in ('verified','limited','dismissed'):
            continue
        attempts=entry['attempts']
        latest=attempts[-1] if attempts else None
        if not latest or latest.get('audit') is not None or latest.get('error'):
            if len(attempts)>=MAX_ATTEMPTS:
                limit(task,latest.get('error') or latest.get('audit',{}).get('reason') or '两次成组修改仍未通过独立核验，未保存该组。')
                checkpoint('propose'); raise JobYieldRequested()
            feedback=(latest.get('error') or latest.get('audit')) if latest else None
            try:
                built=invoke('propose',call_key=f'propose:{gid}:{len(attempts)+1}',task=task,feedback=feedback,
                             previous_candidate=latest.get('candidate') if latest else None)
            except ReviewPending:
                continue
            except ReviewUnitFailed as exc:
                limit(task,str(exc)); checkpoint('propose'); raise JobYieldRequested()
            attempt={'response':built.get('proposal'),'sources':built.get('sources',[])}
            try:
                registry={s['ref']:(pid,s,paper['source_hash']) for pid,paper in board['papers'].items() for s in paper['blocks']}
                for source in attempt['sources']:
                    original=registry.get(source.get('ref'))
                    if not original or source.get('paper_id')!=original[0] or source.get('text')!=original[1]['text'] or source.get('source_hash')!=original[2]:
                        raise ValueError('候选使用的原文不属于冻结证据底稿。')
                response=attempt['response']
                if isinstance(response,dict) and response.get('patches')==[] and response.get('disposition')=='not_needed' and str(response.get('explanation') or '').strip():
                    attempt['candidate']={**task,**response,'read_ids':task['read_ids'],'sources':attempt['sources']}
                else:
                    attempt['candidate']=validate_group(response,task,snapshot['text'],snapshot['blocks'],attempt['sources'])
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                attempt['error']=str(exc)
                if isinstance(attempt['response'],dict) and not attempt['response'].get('patches') and attempt['response'].get('limitation'):
                    attempts.append(attempt); limit(task,str(attempt['response']['limitation']))
                    checkpoint('propose'); raise JobYieldRequested()
            attempts.append(attempt); checkpoint('propose'); raise JobYieldRequested()
        try:
            latest['audit']=invoke('audit',call_key=f'audit:{gid}:{len(attempts)}',task=task,group=latest['candidate'])
        except ReviewPending:
            continue
        except ReviewUnitFailed as exc:
            limit(task,str(exc)); checkpoint('audit'); raise JobYieldRequested()
        if audit_passed(latest['audit'],latest['candidate']):
            entry.update(status='verified' if latest['candidate']['patches'] else 'dismissed',candidate=latest['candidate'],audit=latest['audit'],
                verification=group_check_fingerprint(latest['candidate'],board['hash']))
        elif latest['audit'].get('status')=='blocked':
            limit(task,str(latest['audit'].get('reason') or '原文或作者方向不足以完成安全修改。'))
        checkpoint('audit'); raise JobYieldRequested()
    if any(state['groups'][t['group_id']].get('status') not in ('verified','limited','dismissed') for t in state['plan']['tasks']):
        calls.wait()
    records=[state['groups'][t['group_id']] for t in state['plan']['tasks'] if state['groups'][t['group_id']].get('status')=='verified']
    groups=[r['candidate'] for r in records]
    if groups:
        revised=apply_groups(snapshot['text'],snapshot['blocks'],groups)
        signature=digest({'text':revised,'groups':[r['verification'] for r in records],'board':board['hash']})
        checks=state.setdefault('final_checks',[])
        checked=next((c for c in checks if c['signature']==signature),None)
        if not checked:
            if len(checks)>=2:
                state['limitations'].append('实际保留组合未取得完整一致性结论，所有候选保留但不发布。')
                records=[]; groups=[]
            else:
                try: value=invoke('final',call_key='final:'+signature,revised_text=revised,groups=groups)
                except ReviewPending: calls.wait()
                checked={'signature':signature,'result':value}; checks.append(checked)
                if not final_passed(value,groups):
                    affected=value.get('affected_group_ids',[])
                    if not isinstance(affected,list) or not affected or any(not isinstance(g,str) or g not in {x['group_id'] for x in groups} for g in affected):
                        affected=[g['group_id'] for g in groups]
                    # Any read/write dependency on a reverted group must also be withdrawn.
                    while True:
                        changed={p['block_id'] for g in groups if g['group_id'] in affected for p in g['patches']}
                        more=[g['group_id'] for g in groups if set(g['read_ids'])&changed and g['group_id'] not in affected]
                        if not more: break
                        affected.extend(more)
                    for task in state['plan']['tasks']:
                        if task['group_id'] in affected:
                            limit(task,str(value.get('reason') or '组合核验未通过，未保存该组。'))
                checkpoint('final'); raise JobYieldRequested()
        if groups and checked and not final_passed(checked['result'],groups):
            raise WorkflowValidationError('实际保留组合尚未通过核验。')
        if groups:
            # This is a local source read, not a paid generation. Never reuse a
            # pre-publication freshness check across a queue yield or later retry.
            result=invoke('validate_sources')
            if result.get('unchanged') is not True or result.get('board_hash')!=board['hash']:
                raise WorkflowConflict('原文证据已变化，候选保留，未保存修订稿。')
            state['sources_validated_for']=signature; checkpoint('validate_sources')
            checkpoint('publication')
            state['publication']=publish_review(service,principal,context.project_id,{**payload,'peer_review_job_id':context.job_id},
                records,board_hash=board['hash'],final_check=checked['result'],final_signature=signature)
    state.setdefault('publication',{'changed':False})
    saved=bool(state['publication'].get('changed'))
    state['revisions']=[{**g,'status':'saved' if saved else 'not_saved'} for g in groups]
    state['summary']=state['plan']['summary'] if groups or state['limitations'] else '本轮未发现需要安全修改的有效问题，原稿保持不变；这不代表对全部科学结论作出保证。'
    state['outcome']='revised_with_limits' if saved and state['limitations'] else 'revised' if saved else 'limited' if state['limitations'] else 'no_change_needed'
    checkpoint('completed')
    return state
