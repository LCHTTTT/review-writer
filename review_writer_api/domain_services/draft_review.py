"""Review-style revision: one saved manuscript, exact editing objects, atomic delivery."""
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_api.security import Permission
from review_writer_core.draft_quality import QUALITY_INPUT_ARTIFACTS
from review_writer_core.review_revision import POLICY, audit_passed, final_passed, group_check_fingerprint
from review_writer_core.review_revision_document import digest, document_blocks, apply_groups
from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT


def review_payload(service, principal, project_id, *, guidance='', standard='', expected_draft_artifact_id=''):
    principal.require(Permission.PROJECT_WRITE)
    current=service.get(principal,project_id)
    if not current.get('draft_artifact_id') or not current['paragraphs']:
        raise WorkflowValidationError('请先保存有正文的初稿。')
    if not expected_draft_artifact_id or expected_draft_artifact_id!=current['draft_artifact_id']:
        raise WorkflowConflict('正文已变化，请核对当前已保存版本后再开始审稿式修订。')
    if current['freshness']['upstream_stale']:
        raise WorkflowConflict('请先同步初稿与上游证据，再开始审稿式修订。')
    markdown=current['first_draft_md']
    return {**service.compatibility_payload(principal,project_id,include_figure_files=False),'project_id':project_id,
        'revision_mode':'peer_review','flow_policy':POLICY,'guidance':guidance,'standard':standard,
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
    if (current['first_draft_md']!=payload['snapshot']['text'] or digest(payload['snapshot']['text'])!=payload['snapshot']['sha256']
            or document_blocks(payload['snapshot']['text'])!=payload['snapshot']['blocks']):
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
