"""Versioned section generation, progress, report, and handoff routes."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, status

from review_writer_api.domain_services.sections import SectionsService
from review_writer_api.domain_services.planning import PlanningService
from review_writer_api.job_service import JobService
from review_writer_api.errors import WorkflowConflict
from review_writer_api.routers.jobs import _job_response
from review_writer_api.security import Principal
from review_writer_api.workflow_schemas import (
    BlueprintRestoreRequest,
    BlueprintGenerateRequest,
    BlueprintConfirmRequest,
    SectionsConfirmRequest,
    SectionsGenerateRequest,
)


def build_chapter_planning_router(
    principal_dependency: Callable[..., Principal],
    planning_service: PlanningService,
    job_service: JobService,
    *,
    prefix: str = "/api/v1/projects/{project_id}/sections",
    legacy: bool = False,
) -> APIRouter:
    """Stage 04 owns chapter planning; old URLs share these same handlers."""
    router = APIRouter(prefix=prefix, tags=["sections"], include_in_schema=not legacy)

    def get_chapter_planning(project_id: str, principal: Principal = Depends(principal_dependency)):
        return planning_service.get(principal, project_id)

    if not legacy:
        router.add_api_route("/planning", get_chapter_planning, methods=["GET"])

    @router.post("/blueprint", status_code=status.HTTP_202_ACCEPTED)
    @router.post("/blueprint/jobs", status_code=status.HTTP_202_ACCEPTED)
    def plan_blueprint(
        project_id: str, payload: BlueprintGenerateRequest,
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
        principal: Principal = Depends(principal_dependency),
    ):
        """Both public entries submit the same source-grounded argument planning job."""
        if payload.draft_quality_artifact_id:
            raise WorkflowConflict("Draft argument repairs now run in Draft optimization. Refresh the page and generate a joint revision there.")
        from review_writer_api.application_operations import submit_blueprint
        job = submit_blueprint(planning_service, job_service, principal, project_id,
                               payload.revision, idempotency_key.strip() or str(uuid.uuid4()))
        return _job_response(job)

    @router.post("/blueprint/confirm")
    def confirm_blueprint(
        project_id: str,
        payload: BlueprintConfirmRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return planning_service.confirm_blueprint(
            principal, project_id, revision=payload.revision, artifact_id=payload.artifact_id
        )

    @router.post("/blueprint/restore")
    def restore_blueprint(
        project_id: str,
        payload: BlueprintRestoreRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return planning_service.restore_blueprint(
            principal,
            project_id,
            revision=payload.revision,
            artifact_id=payload.artifact_id,
        )

    return router


def build_sections_router(
    principal_dependency: Callable[..., Principal],
    sections_service: SectionsService,
    job_service: JobService,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/projects/{project_id}/sections", tags=["sections"]
    )

    @router.get("")
    def get_sections(
        project_id: str,
        principal: Principal = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return sections_service.get(principal, project_id)

    @router.get("/status")
    def section_status(project_id: str, principal: Principal = Depends(principal_dependency)):
        return sections_service.status(principal, project_id)

    @router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
    def generate_sections(
        project_id: str,
        _payload: SectionsGenerateRequest,
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
        principal: Principal = Depends(principal_dependency),
    ):
        from review_writer_api.application_operations import submit_sections
        job = submit_sections(sections_service, job_service, principal, project_id,
                              idempotency_key.strip() or str(uuid.uuid4()), restart=_payload.restart)
        return _job_response(job)

    @router.post("/confirm")
    def confirm_sections(
        project_id: str,
        payload: SectionsConfirmRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return sections_service.confirm(
            principal, project_id, revision=payload.revision
        )

    @router.post("/enter")
    def enter_chapter_writing(
        project_id: str,
        principal: Principal = Depends(principal_dependency),
    ) -> dict[str, Any]:
        return sections_service.enter_chapter_writing(principal, project_id)

    return router
