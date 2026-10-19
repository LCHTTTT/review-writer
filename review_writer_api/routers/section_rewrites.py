from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from review_writer_api.domain_services.section_rewrites import SectionRewrites
from review_writer_api.job_service import job_payload

class RewriteChapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    expected_sections_artifact_id: str = Field(min_length=36, max_length=36)


def build_section_rewrites_router(principal_dependency, sections, jobs):
    router = APIRouter(prefix="/api/v1/projects/{project_id}/sections", tags=["sections"])
    service = SectionRewrites(sections)
    @router.post("/{section_id}/rewrite-jobs", status_code=202)
    def rewrite(project_id: str, section_id: str, request: RewriteChapterRequest,
                request_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200), principal=Depends(principal_dependency)):
        sections._owned_project(principal, project_id)
        for previous in jobs.repository.list_project_jobs(principal.user_id, project_id, job_type="sections.generate", limit=100):
            if previous.idempotency_key == request_key:
                from review_writer_api.errors import WorkflowConflict
                saved = previous.payload
                if saved.get("_rewrite_scope") != section_id or (saved.get("tasks") or [{}])[0].get("revision_request") != request.message or (saved.get("_rewrite_expected_artifacts") or {}).get("sections/section_drafts.json") != request.expected_sections_artifact_id:
                    raise WorkflowConflict("This request identity already belongs to different chapter revision inputs.")
                return job_payload(previous)
        payload = service.prepare(principal, project_id, section_id, request.message, request.expected_sections_artifact_id)
        return job_payload(jobs.submit(principal, scope="project", project_id=project_id, job_type="sections.generate", idempotency_key=request_key, payload=payload))
    @router.post("/rewrite-candidates/{candidate_artifact_id}/accept")
    def accept(project_id: str, candidate_artifact_id: str, principal=Depends(principal_dependency)):
        return service.accept(principal, project_id, candidate_artifact_id)
    return router
