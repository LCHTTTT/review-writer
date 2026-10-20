"""Explicit author adoption or rejection of an existing review suggestion.

Adoption is a human manuscript edit, not an AI audit or a source
certification. Rejection records a decision and leaves the manuscript intact.
"""
from pathlib import Path, PurePosixPath

from review_writer_api.domain_services.draft_review import validate_review
from review_writer_api.domain_services.review_activity import active_draft_writer
from review_writer_api.domain_services.review_boards import load_job_board
from review_writer_api.domain_services.review_decisions import rejection, record_rejection
from review_writer_api.errors import WorkflowConflict, WorkflowNotFound, WorkflowValidationError
from review_writer_api.security import Permission
from review_writer_core.review_revision import POLICY, tentative_group, validate_group
from review_writer_core.review_revision_document import apply_groups, digest, document_blocks
from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT
from review_writer_core.draft_quality import QUALITY_INPUT_ARTIFACTS
from review_writer_core.chemical_typography import normalize_chemical_typography


def _candidate(job, group_id, *, allow_incomplete=False):
    result=job.result or {}
    allowed_phases={'propose','completed'} if allow_incomplete else {'completed'}
    if result.get('policy')!=POLICY or result.get('phase') not in allowed_phases:
        raise WorkflowConflict('旧报告不是已完成的当前审稿式修订，不能据此采用候选。')
    task=next((t for t in result.get('plan',{}).get('tasks',[]) if t.get('group_id')==group_id),None)
    if task is None:
        raise WorkflowValidationError('修改组不在原审稿计划中。')
    record=result.get('groups',{}).get(group_id,{})
    candidate=record.get('candidate') if (record.get('candidate') or {}).get('patches') else next(
        (a['candidate'] for a in reversed(record.get('attempts',[])) if (a.get('candidate') or {}).get('patches')),None)
    if not isinstance(candidate,dict) or not candidate.get('patches'):
        raise WorkflowConflict('该组没有可对照的具体修改文字，不能把问题描述当作候选保存。')
    return task,candidate


def _current_source_file(service, principal, record, kind):
    """Resolve the current library file used by the frozen review board."""
    root=service.artifacts.workspace_manager.user_root(principal.user_id).resolve()
    if kind=='mineru_content_list':
        raw=str((record.metadata.get('source_paths') or {}).get('content_list') or '')
        if not raw:
            return None
        candidate=Path(raw)
    elif kind=='mineru_markdown':
        candidate=root / Path(*PurePosixPath(str(record.markdown_relative_path or '')).parts)
    elif kind=='pdf_text':
        candidate=root / Path(*PurePosixPath(str(record.pdf_relative_path or '')).parts)
    else:
        return None
    try:
        path=candidate.resolve()
        path.relative_to(root)
    except (OSError,ValueError):
        return None
    return path if path.is_file() else None


def _verify_source_files(service, principal, board, candidate):
    """Refuse to apply source-based text after its original file changed.

    The board stores a byte hash of the source file copied at review time. It
    is an additional freshness gate; legacy/frozen boards without the field
    remain readable but are not silently treated as current evidence.
    """
    refs={ref for patch in candidate.get('patches',[]) if patch.get('basis') in ('source','author_pdf')
          for ref in patch.get('source_refs',[])}
    if not refs:
        return
    from review_writer_api.domain_services.review_pdf_checks import _library
    library=_library(service)
    for source in candidate.get('sources',[]):
        if source.get('ref') not in refs or source.get('origin')!='author_inspected_original_pdf':
            continue
        expected=str(source.get('source_hash') or '')
        try:
            path=library.file(principal,str(source.get('paper_id') or ''),'pdf')
        except (OSError,WorkflowNotFound) as exc:
            raise WorkflowConflict('审稿后用于该候选的原版 PDF 已不可用，旧候选不能继续保存。') from exc
        if not expected or service.artifacts._sha256(path)!=expected:
            raise WorkflowConflict('审稿后用于该候选的原版 PDF 已变化，旧候选不能继续保存；请重新审稿。')
    pids={source.get('paper_id') for source in candidate.get('sources',[])
          if source.get('ref') in refs and source.get('origin')!='author_inspected_original_pdf'}
    for pid in sorted(pid for pid in pids if pid):
        paper=(board.get('papers') or {}).get(pid) or {}
        expected=str(paper.get('source_sha256') or '')
        kind=str(paper.get('source_kind') or '')
        if not expected or not kind:
            continue
        record=library.get(principal,pid)
        path=_current_source_file(service,principal,record,kind)
        if path is None or service.artifacts._sha256(path)!=expected:
            raise WorkflowConflict('审稿后用于该候选的原始论文文件已变化，旧候选不能继续按原证据保存；请重新审稿。')


def _validate_candidate_sources(job, board, candidate):
    snapshot=job.payload['snapshot']
    registry={s['ref']:(pid,s,paper['source_hash']) for pid,paper in board.get('papers',{}).items()
              for s in paper.get('blocks',[])}
    for source in candidate.get('sources',[]):
        if source.get('origin')=='author_inspected_original_pdf':
            # A prior human PDF check remains version-bound; validate_review
            # rechecks its actual bytes under the publication lock below.
            if source.get('ref') not in {'author-pdf:'+i for i in job.payload.get('pdf_check_ids',[])}:
                raise WorkflowConflict('旧 PDF 核对来源不属于本报告。')
        else:
            original=registry.get(source.get('ref'))
            if not original or source.get('paper_id')!=original[0] or source.get('text')!=original[1]['text'] or source.get('source_hash')!=original[2]:
                raise WorkflowConflict('候选来源与冻结的审稿原文不符。')
    return snapshot


def _active_job(service, principal, project_id, review_job_id):
    job=service.repository.get_job(principal.user_id,review_job_id)
    if (not job or job.project_id!=project_id or job.payload.get('revision_mode')!='peer_review'
            or job.payload.get('flow_policy')!=POLICY):
        raise WorkflowConflict('审稿任务不属于当前账号/项目或已保存的正文版本。')
    author_selects=bool(job.payload.get('author_selects'))
    allowed_statuses={'succeeded','failed','interrupted','cancelled'} if author_selects else {'succeeded'}
    if job.status not in allowed_statuses:
        raise WorkflowConflict('审稿任务尚未结束，候选还不能处理。')
    if active_draft_writer(service,principal.user_id,project_id):
        raise WorkflowConflict('另一个写作或改写任务正在运行，完成后才能处理审稿候选。')
    return job,author_selects


def accept_review_candidates(service, principal, project_id, *, review_job_id, groups,
                             expected_draft_artifact_id):
    """Apply one or more author-approved groups in a single artifact version."""
    principal.require(Permission.PROJECT_WRITE)
    job,author_selects=_active_job(service,principal,project_id,review_job_id)
    if (not groups or len(groups)>16
            or len({item.get('group_id') for item in groups})!=len(groups)):
        raise WorkflowValidationError('请选择不重复的审稿修改组。')
    board=load_job_board(service,principal.user_id,project_id,job)
    if board.get('hash')!=digest({k:v for k,v in board.items() if k!='hash'}):
        raise WorkflowConflict('审稿来源快照不完整，不能作为人工采用的历史依据。')
    selected=[]
    snapshot=None
    for item in groups:
        task,candidate=_candidate(job,item['group_id'],allow_incomplete=author_selects)
        if digest(candidate)!=item['candidate_sha256']:
            raise WorkflowConflict('对照中的候选文字发生变化，未采用另一条修改。')
        if rejection(service.repository.session_factory,principal.user_id,project_id,
                     review_job_id,item['group_id']):
            raise WorkflowConflict('本组已标记为不采纳，不能再同意；如需改判请重新审稿。')
        snapshot=_validate_candidate_sources(job,board,candidate)
        _verify_source_files(service,principal,board,candidate)
        selected.append((task,candidate,item['candidate_sha256']))
    with service._write_lock:
        if any(rejection(service.repository.session_factory,principal.user_id,project_id,
                         review_job_id,item['group_id']) for item in groups):
            raise WorkflowConflict('本组已标记为不采纳，不能再同意；如需改判请重新审稿。')
        original,artifact=service._read_text(principal,project_id,DRAFT_MANUSCRIPT)
        if artifact.id!=expected_draft_artifact_id:
            raise WorkflowConflict('正文版本已更新，请重新查看当前稿件后再决定。')
        current=service.get(principal,project_id)
        if current['freshness'].get('editing_blocked'):
            raise WorkflowConflict('正文证据已变化，暂不能保存修订；请先同步上游内容。')
        first=artifact.id==job.payload['source_draft_artifact_id']
        legacy_pdf=bool(job.payload.get('pdf_check_ids'))
        if first:
            if (digest(snapshot['text'])!=snapshot['sha256']
                    or document_blocks(snapshot['text'])!=snapshot['blocks']
                    or (original!=snapshot['text']
                        and normalize_chemical_typography(original)!=snapshot['text'])):
                raise WorkflowConflict('原审稿快照与当前稿件不符。')
            if legacy_pdf:
                # The retired PDF-check flow keeps its stricter frozen-input
                # contract for historical reports; new author-select jobs do
                # not require unrelated upstream artifacts to be unchanged.
                expected=validate_review(service,principal,project_id,job.payload)
            else:
                if current['revision']!=job.payload.get('source_draft_revision'):
                    raise WorkflowConflict('修订期间正文版本已变化，请重新审稿。')
                expected={DRAFT_MANUSCRIPT:artifact.id}
            approved=[]
        else:
            metadata=artifact.metadata
            if (metadata.get('operation')!='author-approved-review-candidate'
                    or metadata.get('author_approved_from_review_job_id')!=review_job_id
                    or metadata.get('author_approved_source_draft_artifact_id')!=job.payload['source_draft_artifact_id']):
                raise WorkflowConflict('稿件已发生非本报告的修改；旧候选不能覆盖新稿。')
            approved=list(metadata.get('author_approved_group_ids',[]))
            if any(item[1]['group_id'] in approved for item in selected):
                raise WorkflowConflict('本组已经同意，不可重复采用。')
            if legacy_pdf:
                expected=service.validate_artifact_inputs(principal,project_id,job.payload,QUALITY_INPUT_ARTIFACTS)
            else:
                expected={DRAFT_MANUSCRIPT:artifact.id}
            expected[DRAFT_MANUSCRIPT]=artifact.id
        # An unchanged object hash makes a second, disjoint group safe after
        # earlier approvals. Overlapping or manually edited objects conflict.
        blocks=document_blocks(original)
        current_blocks={block['block_id']:block for block in blocks}
        frozen_blocks={block['block_id']:block for block in snapshot['blocks']}
        prepared=[]
        try:
            for task,candidate,candidate_sha256 in selected:
                rebased=[]
                for patch in candidate['patches']:
                    bid=patch['block_id']; frozen=frozen_blocks[bid]; now=current_blocks[bid]
                    if (patch['before_sha256']!=frozen['sha256']
                            or (now['sha256']!=frozen['sha256']
                                and normalize_chemical_typography(now['text'])!=frozen['text'])):
                        raise ValueError('修改对象的原文已变化，不能使用旧候选。')
                    # Older reviews used the normalized display snapshot.
                    # Rebind only the selected object's hash; all other raw
                    # manuscript bytes remain intact when published below.
                    rebased.append({**patch,'before_sha256':now['sha256']})
                proposal={'explanation':candidate['explanation'],'patches':rebased}
                if candidate.get('unverified_tentative'):
                    prepared.append(tentative_group(proposal,task,original,blocks))
                else:
                    prepared.append(validate_group(proposal,task,original,blocks,candidate.get('sources',[])))
            updated=apply_groups(original,blocks,prepared,human_approved=True)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            raise WorkflowConflict('候选对象已变化或与另一组冲突；请重新审稿，原稿未修改。') from exc
        new_ids=[group['group_id'] for group in prepared]
        changed={p['block_id'] for group in prepared for p in group['patches']}
        single=len(selected)==1
        metadata={**artifact.metadata,'operation':'author-approved-review-candidate',
            'author_approved_from_review_job_id':review_job_id,
            'author_approved_source_draft_artifact_id':job.payload['source_draft_artifact_id'],
            'author_approved_group_ids':[*approved,*new_ids],
            'author_approved_group_id':new_ids[0] if single else 'batch',
            'author_approved_candidate_sha256':(selected[0][2] if single else digest([
                {'group_id':task['group_id'],'candidate_sha256':sha} for task,_,sha in selected])),
            'author_approval_is_not_ai_verification':True,'previous_draft_artifact_id':artifact.id}
        metadata['unverified_manual_paragraph_ids']=sorted(set(metadata.get('unverified_manual_paragraph_ids',[]))|
            {bid.removeprefix('paragraph:') for bid in changed if bid.startswith('paragraph:')})
        metadata['content_records']={pid:row for pid,row in metadata.get('content_records',{}).items()
            if 'paragraph:'+pid not in changed}
        if 'heading:0' in changed: metadata['title_user_modified']=True
        published,state=service._publish_files(principal,project_id,{DRAFT_MANUSCRIPT:(updated.encode('utf-8'),'markdown')},
            expected_revision=service.get(principal,project_id)['revision'],expected_current_artifacts=expected,
            metadata=metadata,invalidate_final=True)
    return {'changed':True,'draft_artifact_id':published[DRAFT_MANUSCRIPT].id,
            'revision':state.revision,'author_approved_group_ids':[*approved,*new_ids],
            'author_approved_group_id':new_ids[0] if single else 'batch',
            'groups_saved':len(new_ids),'saved_objects':len(changed),
            'verification':'human_approved_not_ai_certified'}


def accept_review_candidate(service, principal, project_id, *, review_job_id, group_id,
                            expected_draft_artifact_id, candidate_sha256):
    return accept_review_candidates(service,principal,project_id,review_job_id=review_job_id,
        groups=[{'group_id':group_id,'candidate_sha256':candidate_sha256}],
        expected_draft_artifact_id=expected_draft_artifact_id)


def reject_review_candidate(service, principal, project_id, *, review_job_id, group_id,
                            expected_draft_artifact_id, candidate_sha256):
    """Keep the original for this group; no manuscript artifact is written."""
    principal.require(Permission.PROJECT_WRITE)
    job,author_selects=_active_job(service,principal,project_id,review_job_id)
    _task,candidate=_candidate(job,group_id,allow_incomplete=author_selects)
    if digest(candidate)!=candidate_sha256:
        raise WorkflowConflict('对照中的候选文字发生变化，未记录另一条决定。')
    with service._write_lock:
        _original,artifact=service._read_text(principal,project_id,DRAFT_MANUSCRIPT)
        if artifact.id!=expected_draft_artifact_id:
            raise WorkflowConflict('正文版本已更新，请重新查看当前稿件后再决定。')
        metadata=artifact.metadata
        accepted=(list(metadata.get('author_approved_group_ids',[]))
            if metadata.get('operation')=='author-approved-review-candidate'
            and metadata.get('author_approved_from_review_job_id')==review_job_id else [])
        if group_id in accepted:
            raise WorkflowConflict('本组已经同意，不能再标记为不采纳。')
        try:
            return record_rejection(service.repository.session_factory,user_id=principal.user_id,
                project_id=project_id,review_job_id=review_job_id,
                draft_artifact_id=expected_draft_artifact_id,group_id=group_id,
                candidate_sha256=candidate_sha256)
        except ValueError as exc:
            raise WorkflowConflict('该组候选已变化，未记录不采纳决定。') from exc
