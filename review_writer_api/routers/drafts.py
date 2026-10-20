"""Versioned Draft assembly, editing, quality, rewrite, and approval endpoints."""

from __future__ import annotations

import uuid
import asyncio
import json
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse

from review_writer_api.domain_services.drafts import DraftsService
from review_writer_api.job_service import JobService
from review_writer_api.routers.jobs import _job_response
from review_writer_api.security import Principal
from review_writer_api.workflow_schemas import (
    DraftApprovalRequest, DraftPeerReviewRequest, DraftReviewCandidateAcceptRequest,
    DraftReviewCandidateBatchAcceptRequest,
    DraftDialogueRequest,
    DraftSectionDialogueRequest,
    DraftParagraphSaveRequest,
    DraftRestoreRequest,
    DraftTextSaveRequest,
    FinalOverviewTextRequest,
    DraftManuscriptFieldsRequest,
    DraftPublicationRequest,
    DraftOverviewGenerateRequest, DraftOverviewAdoptRequest, DraftSynthesisAdoptRequest,
)


def build_drafts_router(
    principal_dependency: Callable[..., Principal],
    drafts_service: DraftsService,
    job_service: JobService,
    final_service=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/draft", tags=["draft"])

    @router.get("/overview")
    def overview_state(project_id: str, principal: Principal = Depends(principal_dependency)):
        from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT, FINAL_OVERVIEW_IMAGE
        result = final_service.overview_state(principal, project_id)
        draft = drafts_service._artifact(principal, project_id, DRAFT_MANUSCRIPT)
        image = drafts_service._artifact(principal, project_id, FINAL_OVERVIEW_IMAGE)
        jobs = drafts_service.repository.list_project_jobs(principal.user_id, project_id,
            job_type="final.overview", limit=1)
        return {**result,
                "history": final_service.overview_history(principal, project_id),
                "job": _job_response(jobs[0]) if jobs else None,
                "source_changed": bool(image and (not draft or image.metadata.get("source_draft_artifact_id") != draft.id))}

    @router.post("/overview-jobs", status_code=status.HTTP_202_ACCEPTED)
    def generate_overview(project_id: str, payload: DraftOverviewGenerateRequest = DraftOverviewGenerateRequest(), idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                          principal: Principal = Depends(principal_dependency)):
        # Keep the existing persisted job name for gateway/queue compatibility; one implementation.
        from review_writer_api.application_operations import generate_overview
        return _job_response(generate_overview(final_service, job_service, principal, project_id, payload,
            idempotency_key.strip() or str(uuid.uuid4())))

    @router.post("/overview/adopt")
    def adopt_overview(project_id: str, payload: DraftOverviewAdoptRequest,
                      principal: Principal = Depends(principal_dependency)):
        return final_service.adopt_overview(principal, project_id, **payload.model_dump())

    @router.post("/synthesis-candidates/{candidate_id}/accept")
    def adopt_synthesis(project_id: str, candidate_id: str, payload: DraftSynthesisAdoptRequest,
                        principal: Principal = Depends(principal_dependency)):
        return drafts_service.decide_synthesis(principal, project_id, candidate_id, decision="accept",
            expected_base_text_sha256=payload.base_text_sha256, edited_text=payload.text)

    @router.get("/publication")
    def publication_metadata(project_id: str, principal: Principal = Depends(principal_dependency)):
        # Publication metadata is authored in Draft and only assembled by Final.
        return final_service.publication_metadata(principal, project_id)

    @router.put("/publication")
    def save_publication(project_id: str, payload: DraftPublicationRequest,
                        principal: Principal = Depends(principal_dependency)):
        return final_service.save_publication_metadata(principal, project_id,
            revision=payload.revision, authors=list(payload.authors),
            affiliations=list(payload.affiliations))

    @router.put("/overview-text")
    def caption(project_id: str, payload: FinalOverviewTextRequest,
                principal: Principal = Depends(principal_dependency)):
        return final_service.save_overview_text(principal, project_id, **payload.model_dump())

    @router.get("/section-dialogues/{section_id}")
    def get_section_dialogue(project_id: str, section_id: str, principal: Principal = Depends(principal_dependency)):
        return drafts_service.section_dialogue_history(principal, project_id, section_id)

    @router.get("/section-dialogues/{section_id}/versions")
    def section_versions(project_id: str, section_id: str, principal: Principal = Depends(principal_dependency)):
        return drafts_service.section_versions(principal, project_id, section_id)

    @router.get("/section-dialogues/{section_id}/versions/{artifact_id}")
    def section_version(project_id: str, section_id: str, artifact_id: str, principal: Principal = Depends(principal_dependency)):
        return drafts_service._section_version(principal, project_id, section_id, artifact_id)

    @router.post("/section-dialogues/{section_id}", status_code=status.HTTP_202_ACCEPTED)
    def start_section_dialogue(project_id: str, section_id: str, payload: DraftSectionDialogueRequest,
                               idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                               principal: Principal = Depends(principal_dependency)):
        key = idempotency_key.strip() or str(uuid.uuid4())
        job_payload, existing = drafts_service.section_dialogue_payload(principal, project_id, section_id,
            **payload.model_dump(), idempotency_key=key)
        return _job_response(existing or job_service.submit(principal, scope="project", project_id=project_id,
            job_type="draft.optimize", operation_key=f"section:{section_id}", idempotency_key=key, payload=job_payload))

    @router.get("/section-dialogues/{section_id}/stream/{job_id}")
    def stream_section_dialogue(project_id: str, section_id: str, job_id: str, request: Request,
                               principal: Principal = Depends(principal_dependency)):
        # Authenticate and scope-check before sending any streaming headers.
        snapshot = drafts_service.section_stream_snapshot(principal, project_id, section_id, job_id)

        async def events():
            current, previous = snapshot, None
            # Full text snapshots make reconnection idempotent: never append twice.
            for tick in range(240):
                if await request.is_disconnected():
                    return
                encoded = json.dumps(current, ensure_ascii=False)
                if encoded != previous:
                    yield f"event: snapshot\ndata: {encoded}\n\n"
                    previous = encoded
                elif tick % 20 == 0:
                    yield ": keepalive\n\n"
                if current["status"] not in {"queued", "running", "cancel_requested"}:
                    yield "event: done\ndata: {}\n\n"
                    return
                await asyncio.sleep(0.5)
                current = await asyncio.to_thread(drafts_service.section_stream_snapshot,
                    principal, project_id, section_id, job_id)

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @router.get("/dialogues/{paragraph_key}")
    def get_dialogue(project_id: str, paragraph_key: str, principal: Principal = Depends(principal_dependency)):
        return drafts_service.dialogue_history(principal, project_id, paragraph_key)

    @router.post("/dialogues/{paragraph_key}", status_code=status.HTTP_202_ACCEPTED)
    def start_dialogue(project_id: str, paragraph_key: str, payload: DraftDialogueRequest,
                       idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                       principal: Principal = Depends(principal_dependency)):
        key = idempotency_key.strip() or str(uuid.uuid4())
        job_payload, existing = drafts_service.dialogue_payload(principal, project_id, paragraph_key,
            **payload.model_dump(), idempotency_key=key)
        return _job_response(existing or job_service.submit(principal, scope="project", project_id=project_id,
            job_type="draft.rewrite", operation_key=f"paragraph:{paragraph_key}", idempotency_key=key, payload=job_payload))

    @router.post("/dialogue-candidates/{candidate_id}/{decision}")
    def decide_dialogue(project_id: str, candidate_id: str, decision: str,
                        if_match: str | None = Header(default=None, alias="If-Match"),
                        principal: Principal = Depends(principal_dependency)):
        return drafts_service.decide_dialogue(principal, project_id, candidate_id, decision=decision,
            expected_base_text_sha256=if_match)

    @router.post('/review-candidates/{review_job_id}/{group_id}/accept')
    def accept_review_group(project_id: str,review_job_id: str,group_id: str,
                            payload: DraftReviewCandidateAcceptRequest,
                            principal: Principal = Depends(principal_dependency)):
        from review_writer_api.domain_services.review_candidate_acceptance import accept_review_candidate
        return accept_review_candidate(drafts_service,principal,project_id,review_job_id=review_job_id,
            group_id=group_id,**payload.model_dump())

    @router.post('/review-candidates/{review_job_id}/{group_id}/reject')
    def reject_review_group(project_id: str,review_job_id: str,group_id: str,
                            payload: DraftReviewCandidateAcceptRequest,
                            principal: Principal = Depends(principal_dependency)):
        from review_writer_api.domain_services.review_candidate_acceptance import reject_review_candidate
        return reject_review_candidate(drafts_service,principal,project_id,review_job_id=review_job_id,
            group_id=group_id,**payload.model_dump())

    @router.post('/review-candidates/{review_job_id}/accept-selected')
    def accept_review_groups(project_id: str,review_job_id: str,
                             payload: DraftReviewCandidateBatchAcceptRequest,
                             principal: Principal = Depends(principal_dependency)):
        from review_writer_api.domain_services.review_candidate_acceptance import accept_review_candidates
        return accept_review_candidates(drafts_service,principal,project_id,review_job_id=review_job_id,
            groups=[item.model_dump() for item in payload.groups],
            expected_draft_artifact_id=payload.expected_draft_artifact_id)

    @router.post("/review-jobs", status_code=status.HTTP_202_ACCEPTED)
    def start_review(project_id: str, payload: DraftPeerReviewRequest,
                     idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                     principal: Principal = Depends(principal_dependency)):
        from review_writer_api.domain_services.draft_review import review_payload
        from review_writer_api.errors import WorkflowConflict
        key = idempotency_key.strip() or str(uuid.uuid4())
        existing = drafts_service.repository.list_project_jobs(principal.user_id, project_id,
            job_type="draft.optimize", operation_key="peer-review", idempotency_key=key, limit=1)
        if existing:
            from review_writer_core.review_revision import POLICY
            if (existing[0].payload.get('flow_policy') != POLICY
                    or existing[0].payload.get('guidance', '') != payload.guidance
                    or existing[0].payload.get('standard', '') != payload.standard
                    or existing[0].payload.get('source_draft_artifact_id') != payload.expected_draft_artifact_id):
                raise WorkflowConflict("该请求标识已用于不同审稿范围。")
            return _job_response(existing[0])
        prepared = review_payload(drafts_service, principal, project_id, **payload.model_dump())
        return _job_response(job_service.submit(principal, scope="project", project_id=project_id,
            job_type="draft.optimize", operation_key="peer-review", idempotency_key=key, payload=prepared))

    @router.post("/dialogue-batch", status_code=status.HTTP_202_ACCEPTED)
    def start_dialogue_batch(project_id: str, idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                             principal: Principal = Depends(principal_dependency)):
        current = drafts_service.get(principal, project_id)
        key = idempotency_key.strip() or str(uuid.uuid4())
        existing = drafts_service.repository.list_project_jobs(principal.user_id, project_id,
            job_type="draft.optimize", idempotency_key=key, limit=1)
        if existing:
            return _job_response(existing[0])
        from review_writer_api.errors import WorkflowValidationError
        if not current["paragraphs"] or current["freshness"]["upstream_stale"]:
            raise WorkflowValidationError("A current saved Draft is required.")
        from review_writer_core.manuscript_coherence import manuscript_snapshot
        return _job_response(job_service.submit(principal, scope="project", project_id=project_id,
            job_type="draft.optimize", idempotency_key=key, payload={"project_id": project_id,
                "revision_mode": "dialogue_batch", "paragraphs": current["paragraphs"],
                "manuscript_snapshot": manuscript_snapshot(current["paragraphs"], current.get("sections", []),
                    records=current.get("content_records"), repetition=current.get("narrative_repetition")),
                "message": "Analyze and improve this paragraph using the available original-source evidence. Keep the original when no defensible improvement is needed."}))

    @router.post("/dialogue-batch/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    def resume_dialogue_batch(project_id: str, job_id: str,
                              idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                              principal: Principal = Depends(principal_dependency)):
        from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
        source = job_service.status(principal, job_id)
        if (source.project_id != project_id or source.job_type != "draft.optimize"
                or source.payload.get("revision_mode") != "dialogue_batch"):
            raise WorkflowValidationError("This is not a batch revision for the selected project.")
        if source.status != "succeeded" or not any(
                result.get("status") in {"failed", "skipped"}
                for result in (source.result or {}).get("paragraph_results", {}).values()):
            raise WorkflowConflict("This batch has no unfinished paragraphs to resume.")
        current = drafts_service.get(principal, project_id)
        if current["freshness"]["upstream_stale"]:
            raise WorkflowConflict("The manuscript evidence changed. Start a new batch analysis.")
        def identity(paragraph):
            return paragraph.get("paragraph_key"), paragraph.get("paragraph_id"), paragraph.get("text_sha256")
        if [identity(p) for p in current["paragraphs"]] != [identity(p) for p in source.payload.get("paragraphs", [])]:
            raise WorkflowConflict("The saved manuscript changed. Start a new batch analysis.")
        key = idempotency_key.strip() or str(uuid.uuid4())
        return _job_response(job_service.submit(principal, scope="project", project_id=project_id,
            job_type="draft.optimize", idempotency_key=key, payload=source.payload,
            retry_of_job_id=source.id))
    @router.get("")
    def get_draft(
        project_id: str,
        review_job_id: str = "",
        principal: Principal = Depends(principal_dependency),
    ) -> dict:
        return drafts_service.get(principal, project_id, review_job_id=review_job_id)

    @router.post("/assemble")
    def assemble_draft(
        project_id: str,
        principal: Principal = Depends(principal_dependency),
    ) -> dict:
        from review_writer_api.application_operations import assemble_draft
        return assemble_draft(drafts_service, job_service, principal, project_id)

    @router.post("/synthesis/{role}", status_code=status.HTTP_202_ACCEPTED)
    def start_synthesis(project_id: str, role: str,
                        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                        principal: Principal = Depends(principal_dependency)):
        if role not in {"abstract", "conclusion"}:
            from review_writer_api.errors import WorkflowValidationError
            raise WorkflowValidationError("Unknown synthesis section.")
        drafts_service._owned_project(principal, project_id)
        key = idempotency_key.strip() or str(uuid.uuid4())
        existing = drafts_service.repository.list_project_jobs(principal.user_id, project_id,
            job_type="draft.optimize", idempotency_key=key, limit=1)
        if existing:
            if existing[0].payload.get("revision_mode") != "section_synthesis" or existing[0].payload.get("roles") != [role]:
                from review_writer_api.errors import WorkflowConflict
                raise WorkflowConflict("Idempotency key belongs to another operation.")
            return _job_response(existing[0])
        payload = drafts_service.synthesis_payload(principal, project_id, role)
        payload["candidate_seed"] = str(uuid.uuid5(uuid.NAMESPACE_URL, project_id+":"+key))
        return _job_response(job_service.submit(principal, scope="project", project_id=project_id,
            job_type="draft.optimize", operation_key="synthesis:"+role,
            idempotency_key=key, payload=payload))

    @router.put("")
    def save_draft(
        project_id: str,
        payload: DraftTextSaveRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict:
        return drafts_service.save_text(
            principal,
            project_id,
            text=payload.text,
            revision=payload.revision,
        )

    @router.put("/manuscript-fields")
    def save_manuscript_fields(project_id: str, payload: DraftManuscriptFieldsRequest,
                               principal: Principal = Depends(principal_dependency)):
        from review_writer_api.application_operations import save_manuscript_fields
        return save_manuscript_fields(drafts_service, principal, project_id,
                title=payload.title, keywords=payload.keywords, revision=payload.revision)

    @router.put("/paragraphs/{paragraph_id}")
    def save_paragraph(
        project_id: str,
        paragraph_id: str,
        payload: DraftParagraphSaveRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict:
        return drafts_service.save_paragraph(
            principal,
            project_id,
            paragraph_id,
            text=payload.text,
            revision=payload.revision,
            base_text_sha256=payload.base_text_sha256,
        )

    @router.post("/restore")
    def restore_draft(
        project_id: str,
        payload: DraftRestoreRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict:
        return drafts_service.restore(
            principal,
            project_id,
            artifact_id=payload.artifact_id,
            revision=payload.revision,
        )

    # Retired HTTP writes must not silently create scored or auto-applied jobs.
    # Old task executors were removed after the deployment queue drained.
    def retired_revision(project_id: str, principal: Principal = Depends(principal_dependency)):
        from fastapi import HTTPException
        drafts_service._owned_project(principal, project_id)
        raise HTTPException(status_code=410,
            detail="Scored revision was replaced by paragraph dialogue and batch analysis. Refresh Draft to use the current workflow.")

    for path in ("/evaluation-jobs", "/optimization-jobs", "/paragraphs/{paragraph_id}/rewrite-jobs",
                 "/optimization-proposals/{proposal_id}/{decision}", "/rewrite-candidates/{candidate_id}/accept-jobs",
                 "/rewrite-candidates/{candidate_id}/{decision}"):
        router.add_api_route(path, retired_revision, methods=["POST"], deprecated=True)

    @router.post("/approve")
    def approve_draft(
        project_id: str,
        payload: DraftApprovalRequest,
        principal: Principal = Depends(principal_dependency),
    ) -> dict:
        return drafts_service.approve(
            principal,
            project_id,
            revision=payload.revision,
            override_low_score=payload.override_low_score,
            override_reason=payload.override_reason,
        )

    return router
