"""Review-style revision: one saved manuscript, exact editing objects, atomic delivery."""
from review_writer_api.domain_services.review_activity import active_draft_writer
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_api.security import Permission
from review_writer_core.draft_quality import QUALITY_INPUT_ARTIFACTS
from review_writer_core.review_revision import POLICY, audit_passed, final_passed, group_check_fingerprint
from review_writer_core.review_revision_document import digest, document_blocks, apply_groups
from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT


def review_payload(service, principal, project_id, *, guidance='', standard='', expected_draft_artifact_id='',
                   source_review_job_id='', pdf_check_ids=None, recommendation_job_id='', preview_only=False,
                   group_id='', paper_id='', author_selects=True):
    principal.require(Permission.PROJECT_WRITE)
    current=service.get(principal,project_id)
    if not current.get('draft_artifact_id') or not current['paragraphs']:
        raise WorkflowValidationError('请先保存有正文的初稿。')
    if active_draft_writer(service,principal.user_id,project_id):
        raise WorkflowConflict('请先等待当前写作或改写任务结束，再开始审稿；并发修改会使候选失效。')
    if not expected_draft_artifact_id or expected_draft_artifact_id!=current['draft_artifact_id']:
        raise WorkflowConflict('正文已变化，请核对当前已保存版本后再开始审稿式修订。')
    if current['freshness']['upstream_stale']:
        raise WorkflowConflict('请先同步初稿与上游证据，再开始审稿式修订。')
    checks=[];suggest_pdf_sha256=''
    pdf_check_ids=pdf_check_ids or []
    if preview_only:
        if not source_review_job_id or not group_id or not paper_id or pdf_check_ids:
            raise WorkflowValidationError('AI 修改建议须对应原审稿的一个未交付组和相关论文。')
    elif bool(source_review_job_id)!=bool(pdf_check_ids):
        raise WorkflowValidationError('定向续改需要匹配的原审稿任务与 PDF 核对记录。')
    if source_review_job_id:
        source=service.repository.get_job(principal.user_id,source_review_job_id)
        if (source is None or source.project_id!=project_id or source.payload.get('flow_policy')!=POLICY
                or source.payload.get('source_draft_artifact_id')!=expected_draft_artifact_id
                or source.status not in ('succeeded','failed','interrupted','cancelled')
                or not (source.result or {}).get('plan')):
            raise WorkflowConflict('原审稿计划不属于当前稿件，不能定向续改。')
        if preview_only:
            task=next((t for t in source.result['plan']['tasks'] if t['group_id']==group_id),None)
            if (not task or source.result.get('groups',{}).get(group_id,{}).get('status')!='limited'
                    or paper_id not in task.get('paper_ids',[]) or paper_id not in source.result.get('board',{}).get('papers',{})):
                raise WorkflowConflict('AI 建议不属于当前原审稿未交付组绑定的论文。')
            from review_writer_api.domain_services.review_pdf_checks import _library
            library=_library(service)
            original_pdf=library.file(principal,paper_id,'pdf')
            if library._pdf_page_count(original_pdf)<1:
                raise WorkflowValidationError('原版 PDF 无法打开，不能生成无法供用户核对的收费建议。')
            suggest_pdf_sha256=service.artifacts._sha256(original_pdf)
        else:
            from review_writer_api.domain_services.review_pdf_checks import owned_pdf_checks
            checks=owned_pdf_checks(service,principal,project_id,pdf_check_ids,
                draft_artifact_id=expected_draft_artifact_id,review_job_id=source_review_job_id)
            if (len({c['group_id'] for c in checks})!=1 or any(
                    c['group_id'] not in {t['group_id'] for t in source.result['plan']['tasks']}
                    or source.result.get('groups',{}).get(c['group_id'],{}).get('status')!='limited' for c in checks)):
                raise WorkflowConflict('定向续改仅接受原审稿同一个未交付修改组的 PDF 核对记录。')
    if pdf_check_ids and not preview_only:
        recommendation=service.repository.get_job(principal.user_id,recommendation_job_id) if recommendation_job_id else None
        if (recommendation is None or recommendation.project_id!=project_id or recommendation.status!='succeeded'
                or recommendation.payload.get('revision_mode')!='peer_review_recommendation'
                or recommendation.payload.get('source_draft_artifact_id')!=expected_draft_artifact_id
                or recommendation.payload.get('source_review_job_id')!=source_review_job_id
                or recommendation.payload.get('suggest_group_id')!=checks[0]['group_id']
                or recommendation.payload.get('suggest_paper_id')!=checks[0]['paper_id']
                or recommendation.payload.get('suggest_pdf_sha256')!=checks[0]['pdf_sha256']
                or not (recommendation.result or {}).get('recommendation')
                or recommendation.result['recommendation'].get('pdf_quote_guess')!=checks[0]['transcription']
                or recommendation.payload.get('guidance','')!=guidance
                or recommendation.payload.get('standard','')!=standard):
            raise WorkflowConflict('请先取得本稿件与原版 PDF 核对对应的 AI 修改建议，再同意执行。')
    # get() returns typography-normalized display text. The frozen review
    # snapshot must instead be the exact manuscript artifact bytes so that
    # accepting one group never rewrites unrelated chemical typography.
    markdown,source_artifact=service._read_text(principal,project_id,DRAFT_MANUSCRIPT)
    if source_artifact.id!=current['draft_artifact_id']:
        raise WorkflowConflict('初稿版本在准备审稿时发生变化，请重新提交。')
    return {**service.compatibility_payload(principal,project_id,include_figure_files=False),'project_id':project_id,
        'revision_mode':'peer_review','flow_policy':POLICY,'author_selects':bool(author_selects) and not bool(pdf_check_ids),
        'guidance':guidance,'standard':standard,
        'source_review_job_id':source_review_job_id,'pdf_check_ids':pdf_check_ids,'confirmed_pdf_checks':checks,
        'recommendation_job_id':recommendation_job_id,'recommend_only':preview_only,
        'approved_candidate_sha256':digest(recommendation.result['recommendation']['candidate']) if pdf_check_ids else '',
        'suggest_group_id':group_id,'suggest_paper_id':paper_id,'suggest_pdf_sha256':suggest_pdf_sha256,
        'source_draft_artifact_id':current['draft_artifact_id'],'source_draft_revision':current['revision'],
        'draft_text':markdown,'snapshot':{'text':markdown,'sha256':digest(markdown),'blocks':document_blocks(markdown)}}


def validate_review(service, principal, project_id, payload):
    if payload.get('flow_policy')!=POLICY:
        raise WorkflowValidationError('旧审稿执行逻辑已停用。原报告和历史保留；请从当前初稿启动审稿式修订，不会重放旧任务。')
    service._owned_project(principal,project_id)
    expected=service.validate_artifact_inputs(principal,project_id,payload,{
        **QUALITY_INPUT_ARTIFACTS,'source_draft_artifact_id':DRAFT_MANUSCRIPT})
    current=service.get(principal,project_id)
    if current['revision']!=payload['source_draft_revision'] or current['freshness']['upstream_stale']:
        raise WorkflowConflict('修订期间正文或上游状态已变化，候选保留，未覆盖正文。')
    if payload.get('recommend_only'):
        from review_writer_api.domain_services.review_pdf_checks import _library
        path=_library(service).file(principal,payload['suggest_paper_id'],'pdf')
        if service.artifacts._sha256(path)!=payload.get('suggest_pdf_sha256'):
            raise WorkflowConflict('原版 PDF 自生成 AI 候选后发生变化，请重新取得候选。')
    if payload.get('pdf_check_ids'):
        from review_writer_api.domain_services.review_pdf_checks import owned_pdf_checks
        checks=owned_pdf_checks(service,principal,project_id,payload['pdf_check_ids'],
            draft_artifact_id=payload['source_draft_artifact_id'],review_job_id=payload['source_review_job_id'])
        if checks!=payload.get('confirmed_pdf_checks'):
            raise WorkflowConflict('原版 PDF 人工核对记录发生变化，未沿用旧结论。')
    raw,artifact=service._read_text(principal,project_id,DRAFT_MANUSCRIPT)
    if (artifact.id!=payload['source_draft_artifact_id'] or raw!=payload['snapshot']['text']
            or digest(raw)!=payload['snapshot']['sha256']
            or document_blocks(raw)!=payload['snapshot']['blocks']):
        raise WorkflowValidationError('修订底稿或对象指纹损坏，未覆盖正文。')
    return expected


def publish_review(service, principal, project_id, payload, records, *, board_hash, final_check, final_signature):
    principal.require(Permission.PROJECT_WRITE)
    candidates=[r['candidate'] for r in records]
    if not candidates:
        return {'changed':False}
    for record in records:
        candidate=record['candidate']
        if not audit_passed(record['audit'],candidate) or record.get('verification')!=group_check_fingerprint(candidate,board_hash):
            raise WorkflowValidationError('修改组缺少匹配当前底稿和证据版本的独立核验。')
    updated=apply_groups(payload['snapshot']['text'],payload['snapshot']['blocks'],candidates)
    signature=digest({'text':updated,'groups':[r['verification'] for r in records],'board':board_hash})
    if not final_passed(final_check,candidates) or final_signature!=signature:
        raise WorkflowValidationError('实际保留的修订组合未通过一致性核验，未保存。')
    with service._write_lock:
        expected=validate_review(service,principal,project_id,payload)
        original,artifact=service._read_text(principal,project_id,DRAFT_MANUSCRIPT)
        if original!=payload['snapshot']['text']:
            raise WorkflowConflict('初稿已变化，未覆盖当前正文。')
        changed={p['block_id'] for g in candidates for p in g['patches']}
        metadata={**artifact.metadata,'operation':'review-style-revision','peer_review_job_id':payload.get('peer_review_job_id'),
            'previous_draft_artifact_id':artifact.id,'review_revision_policy':POLICY,'review_evidence_board_hash':board_hash,
            'review_revision_text_sha256':digest(updated),
            'review_pdf_check_ids':payload.get('pdf_check_ids',[]),
            'review_approved_recommendation_job_id':payload.get('recommendation_job_id',''),
            'review_approved_candidate_sha256':payload.get('approved_candidate_sha256',''),
            'review_pdf_evidence_origin':'author_inspected_original_pdf' if payload.get('pdf_check_ids') else '',
            'review_revision_groups':[{'group_id':r['candidate']['group_id'],'verification':r['verification'],
                'patches':r['candidate']['patches'],'audit':r['audit']} for r in records]}
        metadata['unverified_manual_paragraph_ids']=[pid for pid in metadata.get('unverified_manual_paragraph_ids',[]) if 'paragraph:'+pid not in changed]
        if 'heading:0' in changed:
            metadata['title_user_modified']=True
        # A changed object's previous assembly-derived content projection is stale.
        metadata['content_records']={pid:row for pid,row in metadata.get('content_records',{}).items() if 'paragraph:'+pid not in changed}
        published,state=service._publish_files(principal,project_id,{DRAFT_MANUSCRIPT:(updated.encode('utf-8'),'markdown')},
            expected_revision=payload['source_draft_revision'],expected_current_artifacts=expected,
            metadata=metadata,invalidate_final=True)
        return {'changed':True,'draft_artifact_id':published[DRAFT_MANUSCRIPT].id,'revision':state.revision,
            'saved_groups':len(records),'saved_objects':len(changed)}
