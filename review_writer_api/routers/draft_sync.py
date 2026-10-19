"""Stage and conversation share the same conservative synchronization service."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from review_writer_api.domain_services.draft_sync import DraftSynchronization

class SynchronizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_artifact_id: str = Field(min_length=36, max_length=36)
    expected_sections_artifact_id: str = Field(min_length=36, max_length=36)


def build_draft_sync_router(principal_dependency, drafts):
    router = APIRouter(prefix="/api/v1/projects/{project_id}/draft/upstream-sync", tags=["draft"])
    service = DraftSynchronization(drafts)
    @router.get("/{section_id}")
    def preview(project_id: str, section_id: str, principal=Depends(principal_dependency)):
        result = service.preview(principal, project_id, section_id)
        return {key: value for key, value in result.items() if key != "upstream_baseline_text"}
    @router.post("/{section_id}")
    def save(project_id: str, section_id: str, payload: SynchronizeRequest, principal=Depends(principal_dependency)):
        return service.save(principal, project_id, section_id, **payload.model_dump())
    return router
