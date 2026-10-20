"""Review-as-revision executor. Retired review/finding/repair loops are not run."""
import re
from copy import deepcopy
from review_writer_api.domain_services.draft_review import validate_review, publish_review
from review_writer_api.domain_services.review_boards import read_board, write_board
from review_writer_api.errors import WorkflowValidationError, WorkflowConflict
from review_writer_api.job_service import JobYieldRequested
from review_writer_api.scientific_runner import ScientificModelDeferred
from review_writer_api.job_handlers.stage_execution import wait_for_delegated_model, yield_to_delegated_model
from review_writer_api.job_handlers.draft_review_calls import ReviewCalls, ReviewPending, ReviewUnitFailed
from review_writer_core.review_revision_schedule import planning_units, validate_coordination, author_pdf_write_scope
from review_writer_api.security import Principal, Role
from review_writer_core.review_revision import (POLICY, MAX_ATTEMPTS, validate_plan, validate_group, tentative_group,
    audit_passed, final_passed, group_check_fingerprint)
from review_writer_core.review_revision_document import digest, apply_groups
from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT


def run_review(service, builder, context, payload, *, planning=None):
    # The public review API creates author_selects jobs only. The pdf_check,
    # recommendation, audit and final branches below are retained for jobs
    # queued by the previous flow and for frozen report replay; they are not
    # reachable from the current HTTP contract.
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
    board=None
    if state.get('board_ref'):
        board=read_board(service,context.user_id,state['board_ref'])
    elif state.get('board'):
        # Lazy migration of an old checkpoint: write the board once and stop
        # persisting it in every subsequent result snapshot.
        board=state.pop('board')
        state['board_ref']=write_board(service,context.user_id,board)
    def checkpoint(phase):
        state['phase']='plan' if phase in ('plan_slice','coordinate') else phase
        pending=[r['job_id'] for r in state.get('model_calls',{}).values() if r.get('status')=='waiting']
        state['waiting_model_job_ids']=pending
        tasks=state.get('plan',{}).get('tasks',[])
        done=sum(g.get('status') in ('verified','limited','dismissed','recommended') for g in state['groups'].values())
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
        request={'stage':stage,'snapshot':snapshot,'board':board,
            'confirmed_pdf_checks':payload.get('confirmed_pdf_checks',[]),
            'standard':payload.get('standard',''),'guidance':payload.get('guidance',''),**extra}
        one={**payload,'revision_mode':'dialogue','dialogue':{'review_revision':request}}
        try:
            value=builder(context,one)
        except ScientificModelDeferred as exc:
            yield_to_delegated_model(context,exc)
        if not isinstance(value,dict):
            raise WorkflowValidationError('修订阶段未返回有效结果，检查点保留，未覆盖正文。')
        return value
    calls=ReviewCalls(context,state,payload,builder,checkpoint,board)
    if board is None:
        board=invoke('prepare').get('board')
        if not isinstance(board,dict) or board.get('manuscript_hash')!=snapshot['sha256'] or board.get('hash')!=digest({k:v for k,v in board.items() if k!='hash'}):
            raise WorkflowValidationError('证据底稿与初稿快照不一致。')
        state['board_ref']=write_board(service,context.user_id,board)
        checkpoint('prepare'); raise JobYieldRequested()
    if payload.get('recommend_only') and 'plan_raw' not in state:
        original=context.repository.get_job(context.user_id,payload['source_review_job_id'])
        if (original is None or original.project_id!=context.project_id or original.payload.get('snapshot')!=snapshot
                or not (original.result or {}).get('plan')):
            raise WorkflowConflict('AI 建议与原审稿计划或冻结稿件不匹配。')
        tasks=[dict(t) for t in original.result['plan']['tasks'] if t['group_id']==payload['suggest_group_id']]
        if (len(tasks)!=1 or original.result.get('groups',{}).get(tasks[0]['group_id'],{}).get('status')!='limited'
                or payload['suggest_paper_id'] not in tasks[0].get('paper_ids',[])):
            raise WorkflowConflict('所选建议不属于原审稿未交付组的论文。')
        state['plan_raw']={'summary':'为原版 PDF 人工对照生成待核验的 AI 候选。','tasks':tasks,'limitations':[]}
        checkpoint('plan');raise JobYieldRequested()
    if payload.get('recommendation_job_id') and 'plan_raw' not in state:
        preview=context.repository.get_job(context.user_id,payload['recommendation_job_id'])
        if (not preview or preview.project_id!=context.project_id or preview.status!='succeeded'
                or preview.payload.get('revision_mode')!='peer_review_recommendation'
                or preview.payload.get('snapshot')!=snapshot
                or (preview.result or {}).get('board',{}).get('hash')!=board['hash']
                or not (preview.result or {}).get('recommendation')):
            raise WorkflowConflict('待同意 AI 建议的稿件、原文或 PDF 核对版本已不匹配。')
        suggested=preview.result['recommendation'];task=suggested['task'];candidate=suggested['candidate']
        checks=payload.get('confirmed_pdf_checks',[])
        quote=suggested.get('pdf_quote_guess','')
        if (len(checks)!=1 or checks[0]['group_id']!=task['group_id']
                or checks[0]['paper_id']!=preview.payload.get('suggest_paper_id')
                or checks[0]['pdf_sha256']!=preview.payload.get('suggest_pdf_sha256')
                or not isinstance(quote,str) or not quote.strip() or quote!=checks[0]['transcription']):
            raise WorkflowConflict('作者同意的 AI 候选引文与已核对 PDF 页面不一致。')
        if (candidate.get('group_id')!=task['group_id'] or not candidate.get('patches')
                or suggested.get('digest')!=digest({k:suggested[k] for k in ('task','candidate','sources','response','pdf_quote_guess')})):
            raise WorkflowConflict('建议内容不完整，不能跳过用户同意直接修改。')
        try:
            planned=validate_plan(preview.result['plan_raw'],snapshot['blocks'],board,markdown=snapshot['text'])
            if (planned!=preview.result['plan'] or len(planned['tasks'])!=1 or planned['tasks'][0]!=task
                    or validate_group(suggested['response'],task,snapshot['text'],snapshot['blocks'],suggested['sources'])!=candidate):
                raise ValueError('待同意建议与冻结计划或正文对象不一致。')
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise WorkflowConflict('AI 建议与核对时的稿件或原文不一致，未执行修改。') from exc
        human={'ref':'author-pdf:'+checks[0]['id'],'paper_id':checks[0]['paper_id'],
            'page':checks[0]['page'],'text':checks[0]['transcription'],
            'source_hash':checks[0]['pdf_sha256'],'origin':'author_inspected_original_pdf',
            'note':'AI 先提出候选引文；用户对照原版 PDF 页面后确认，AI 未目视 PDF。'}
        attested=deepcopy(candidate)
        attested['sources']=[*attested['sources'],human]
        bound=False
        for patch in attested['patches']:
            if quote in patch['after']:
                patch.update(basis='author_pdf',source_refs=[human['ref']],support_ids=[])
                bound=True
        if not bound:
            raise WorkflowConflict('AI 推荐的 PDF 引文不在拟修改文字内，不能作为原文依据。')
        try:
            attested=validate_group({'explanation':attested['explanation'],'patches':attested['patches']},
                task,snapshot['text'],snapshot['blocks'],attested['sources'])
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise WorkflowConflict('已确认的原版 PDF 引文未通过候选证据校验。') from exc
        state['plan_raw']=deepcopy(preview.result['plan_raw'])
        state['plan']=deepcopy(preview.result['plan'])
        state['groups'][task['group_id']]={'attempts':[{'candidate':attested,
            'sources':attested['sources'],'response':deepcopy(suggested['response'])}]} 
        checkpoint('plan');raise JobYieldRequested()
    if payload.get('pdf_check_ids') and 'plan_raw' not in state:
        original=context.repository.get_job(context.user_id,payload['source_review_job_id'])
        if (original is None or original.project_id!=context.project_id or original.payload.get('flow_policy')!=POLICY
                or original.payload.get('snapshot')!=snapshot or not (original.result or {}).get('plan')):
            raise WorkflowConflict('原审稿检查点或冻结稿件不匹配，未重放模型。')
        selected={c['group_id'] for c in payload['confirmed_pdf_checks']}
        tasks=[dict(t) for t in original.result['plan']['tasks'] if t['group_id'] in selected]
        if len(tasks)!=1 or {t['group_id'] for t in tasks}!=selected:
            raise WorkflowConflict('已核对论文未绑定原审稿的单个未交付修改组。')
        original_task=tasks[0]
        try:
            planned=invoke('recoordinate',original_task=original_task,
                previous_composition_feedback=[c['result'].get('reason','') for c in original.result.get('final_checks',[])])
        except ReviewPending: calls.wait()
        candidates=planned.get('tasks')
        if not isinstance(candidates,list) or len(candidates)!=1:
            raise WorkflowValidationError('定向修订必须形成一个完整关联修改组，未猜测其他组的写入范围。')
        compact=validate_plan(planned,snapshot['blocks'],board,markdown=snapshot['text'])
        scope=author_pdf_write_scope(snapshot,board,original_task,payload['confirmed_pdf_checks'])
        if (len(compact['tasks'])!=1 or not set(original_task['write_ids']).issubset(compact['tasks'][0]['write_ids'])
                or {c['paper_id'] for c in payload['confirmed_pdf_checks']}-set(compact['tasks'][0]['paper_ids'])
                or set(compact['tasks'][0]['read_ids']+compact['tasks'][0]['write_ids'])-scope):
            raise WorkflowValidationError('协调计划遗漏原待修订位置，未覆盖稿件。')
        relevant={original_task['group_id']}
        expanded={t['group_id'] for t in original.result['plan']['tasks']
            if t['group_id'] not in relevant and set(t['write_ids'])&set(compact['tasks'][0]['write_ids'])}
        if expanded:
            raise WorkflowValidationError('协调计划与其他审稿组写入位置重叠，需要合并后重新核验，未静默覆盖。')
        compact['tasks'][0]['pdf_check_ids']=[c['id'] for c in payload['confirmed_pdf_checks']]
        state['plan_raw']={'summary':compact['summary'],'tasks':compact['tasks'],'limitations':compact['limitations']}
        checkpoint('plan'); raise JobYieldRequested()
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
    def friendly_reason(value):
        text=str(value or '').strip()
        if '：' in text:
            head,tail=text.split('：',1)
            if re.fullmatch(r'[a-z_]+(?:[:\-][\w.-]+)*',head):
                return tail.strip()
        return text
    def limit(task, reason):
        reason=friendly_reason(reason) or '本组未取得可安全采用的修改。'
        entry=state['groups'][task['group_id']]
        entry.update(status='limited',reason=reason)
        message=task['problem']+'：'+reason
        if message not in state['limitations']:
            state['limitations'].append(message)
    for task in state['plan']['tasks']:
        gid=task['group_id']; entry=state['groups'].setdefault(gid,{'attempts':[]})
        if entry.get('status') in ('verified','limited','dismissed','recommended'):
            continue
        attempts=entry['attempts']
        latest=attempts[-1] if attempts else None
        if payload.get('author_selects') and latest:
            candidate=latest.get('candidate') or {}
            if candidate.get('patches'):
                entry.update(status='recommended',candidate=candidate)
                checkpoint('propose');raise JobYieldRequested()
            response=latest.get('response') if isinstance(latest.get('response'),dict) else {}
            if response.get('disposition')=='not_needed' and str(response.get('explanation') or '').strip():
                # A false suspicion is a valid review result, not an AI delivery
                # failure. Keep the reason visible and do not add a limitation.
                entry.update(status='dismissed',reason=str(response['explanation']).strip())
                checkpoint('propose');raise JobYieldRequested()
            if response.get('disposition')=='blocked' or str(response.get('limitation') or '').strip():
                reason=str(response.get('limitation') or response.get('explanation')
                    or latest.get('error') or '模型未交付可安全采用的修改。').strip()
                limit(task,reason)
                checkpoint('propose');raise JobYieldRequested()
            # Malformed/empty proposals are retried once with feedback below,
            # instead of being turned into a limitation on the first failure.
        if payload.get('recommend_only') and latest and latest.get('error'):
            limit(task,'AI 建议格式或证据未通过校验，不自动追加一次可能计费的建议请求。')
            checkpoint('recommendation_unavailable');return state
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
                human={('author-pdf:'+c['id']):c for c in payload.get('confirmed_pdf_checks',[])
                       if c['id'] in task.get('pdf_check_ids',[])}
                for source in attempt['sources']:
                    attested=human.get(source.get('ref'))
                    if attested:
                        if (source.get('origin')!='author_inspected_original_pdf' or source.get('paper_id')!=attested['paper_id']
                                or source.get('text')!=attested['transcription'] or source.get('page')!=attested['page']
                                or source.get('source_hash')!=attested['pdf_sha256']):
                            raise ValueError('作者 PDF 转录与已确认的原版文件版本不匹配。')
                        continue
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
                if payload.get('author_selects'):
                    try:
                        attempt['candidate']=tentative_group(attempt['response'],task,snapshot['text'],snapshot['blocks'])
                        attempt.pop('error',None)
                    except (ValueError,TypeError,KeyError,AttributeError):
                        pass
                response=attempt['response'] if isinstance(attempt['response'],dict) else {}
                if (payload.get('author_selects') and response.get('disposition')=='not_needed'
                        and str(response.get('explanation') or '').strip()):
                    attempts.append(attempt)
                    entry.update(status='dismissed',reason=str(response['explanation']).strip())
                    checkpoint('propose'); raise JobYieldRequested()
                if response and not response.get('patches') and response.get('limitation'):
                    attempts.append(attempt); limit(task,str(response['limitation']))
                    checkpoint('propose'); raise JobYieldRequested()
            attempts.append(attempt); checkpoint('propose'); raise JobYieldRequested()
        if payload.get('recommend_only'):
            quote=latest.get('response',{}).get('pdf_quote_guess') if isinstance(latest.get('response'),dict) else None
            if (latest.get('candidate',{}).get('patches') and isinstance(quote,str) and quote.strip()
                    and len(quote)<=4000 and any(quote in patch['after'] for patch in latest['candidate']['patches'])):
                state['recommendation']={'task':task,'candidate':latest['candidate'],
                    'sources':latest['sources'],'response':latest['response'],'pdf_quote_guess':quote.strip()}
                state['recommendation']['digest']=digest(state['recommendation'])
                checkpoint('recommendation_ready');return state
            limit(task,'未形成具体的 AI 修改建议，原稿未变。')
            checkpoint('recommendation_unavailable');return state
        try:
            latest['audit']=invoke('audit',call_key=f'audit:{gid}:{len(attempts)}',task=task,group=latest['candidate'])
        except ReviewPending:
            continue
        except ReviewUnitFailed as exc:
            limit(task,str(exc)); checkpoint('audit'); raise JobYieldRequested()
        if audit_passed(latest['audit'],latest['candidate']):
            entry.update(status='verified' if latest['candidate']['patches'] else 'dismissed',candidate=latest['candidate'],audit=latest['audit'],
                verification=group_check_fingerprint(latest['candidate'],board['hash']))
        elif payload.get('recommendation_job_id') or latest['audit'].get('status')=='blocked':
            limit(task,str(latest['audit'].get('reason') or '作者同意的具体建议未通过独立核验；不自动改成另一条未经同意的建议。'))
        checkpoint('audit'); raise JobYieldRequested()
    if any(state['groups'][t['group_id']].get('status') not in ('verified','limited','dismissed','recommended') for t in state['plan']['tasks']):
        calls.wait()
    if payload.get('author_selects'):
        state['publication']={'changed':False}
        records=[state['groups'][t['group_id']] for t in state['plan']['tasks']]
        state['revisions']=[{**r['candidate'],'status':'not_saved'} for r in records
            if r.get('status')=='recommended' and (r.get('candidate') or {}).get('patches')]
        dismissed=sum(r.get('status')=='dismissed' for r in records)
        summary=str(state['plan'].get('summary') or '').strip()
        if state['revisions']:
            state['outcome']='awaiting_author_choice'
            state['summary']=summary or f'本轮生成 {len(state["revisions"])} 组待确认的修改候选，原稿尚未改变。'
        elif state['limitations']:
            state['outcome']='limited'
            state['summary']=summary or '本轮没有交付可安全采纳的修改，原稿保持不变；请查看限制说明。'
        else:
            state['outcome']='no_change_needed'
            state['summary']=summary or ('经复核，当前疑点无需修改，原稿保持不变。' if dismissed
                else '本轮未发现需要安全修改的有效问题，原稿保持不变；这不代表对全部科学结论作出保证。')
        checkpoint('completed');return state
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
