from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from review_writer_api.domain_services.planning_field_edits import patch_blueprint_fields

class BlueprintFieldPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_artifact_id: str = Field(min_length=36, max_length=36)
    fields: dict[str, str] = Field(min_length=1, max_length=2)
    save: bool = True


def build_planning_field_router(principal_dependency, planning):
    router = APIRouter(prefix="/api/v1/projects/{project_id}/planning/blueprint/sections", tags=["planning"])
    @router.patch("/{section_id}/fields")
    def patch(project_id: str, section_id: str, payload: BlueprintFieldPatch, principal=Depends(principal_dependency)):
        return patch_blueprint_fields(planning, principal, project_id, section_id=section_id, **payload.model_dump())
    return router
