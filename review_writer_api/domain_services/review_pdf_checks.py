"""Owner-scoped, append-only human readings of original PDFs.

No parsed Markdown, AI output or PDF metadata is treated as a transcription.
The current review HTTP contract has no route that creates these checks; they
are retained for jobs queued by the previous PDF-check flow and frozen report
replay. Do not treat this module as a new review entry point.
"""
import hashlib
import uuid
from sqlalchemy import select
from review_writer_api.database import database_session
from review_writer_api.domain_services.library import LibraryService
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError, WorkflowNotFound
from review_writer_api.security import Permission
from review_writer_api.workflow_models import ReviewPdfCheck


def _library(service):
    return LibraryService(service.repository.session_factory,service.artifacts.workspace_manager)


def _pdf_hash(service,principal,paper_id,*,page=None):
    library=_library(service)
    path=library.file(principal,paper_id,'pdf')
    if page is not None:
        count=library._pdf_page_count(path)
        if count==0 or page>count:
            raise WorkflowValidationError('原版 PDF 页码无效或文件无法读取；未记录未经核对的页面。')
    return service.artifacts._sha256(path)


def check_payload(row):
    return {'id':str(row.id),'review_job_id':str(row.review_job_id),'draft_artifact_id':str(row.draft_artifact_id),
        'group_id':row.group_id,'paper_id':row.paper_id,'pdf_sha256':row.pdf_sha256,
        'page':row.page,'transcription':row.transcription,'note':row.note,'origin':'author_inspected_original_pdf'}


def owned_pdf_checks(service,principal,project_id,ids,*,draft_artifact_id,review_job_id):
    principal.require(Permission.PROJECT_READ)
    service._owned_project(principal,project_id)
    if len(ids)>16 or len(set(ids))!=len(ids):
        raise WorkflowValidationError('PDF 核对记录重复或过多。')
    checks=[]
    with database_session(service.repository.session_factory) as session:
        for raw in ids:
            try:check_id=uuid.UUID(raw)
            except (ValueError,TypeError,AttributeError) as exc:raise WorkflowValidationError('PDF 核对记录 ID 无效。') from exc
            row=session.scalar(select(ReviewPdfCheck).where(ReviewPdfCheck.id==check_id,
                ReviewPdfCheck.user_id==uuid.UUID(principal.user_id),ReviewPdfCheck.project_id==uuid.UUID(project_id),
                ReviewPdfCheck.review_job_id==uuid.UUID(review_job_id),
                ReviewPdfCheck.draft_artifact_id==uuid.UUID(draft_artifact_id)))
            if row is None:raise WorkflowNotFound('当前稿件的 PDF 核对记录不存在。')
            checks.append(check_payload(row))
    for row in checks:
        if _pdf_hash(service,principal,row['paper_id'],page=row['page'])!=row['pdf_sha256']:
            raise WorkflowConflict('原始 PDF 已变化，旧人工核对不能继续用于修订。')
    return checks
