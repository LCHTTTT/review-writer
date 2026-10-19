"""Shared application operations used by HTTP and project conversation.

Own submission side effects here, not in the conversation runner. Preserve
existing service validation, task keys and partial-success semantics.
"""
from __future__ import annotations

import uuid

from review_writer_api.errors import WorkflowConflict, WorkflowError
from review_writer_api.job_service import job_payload
from review_writer_api.planning_jobs import queue_matrix_enrichment


def job_belongs_to_project(job, project_id):
    """Called only after resolving a user-owned job; acquisition is library-scoped."""
    return bool(job and (job.project_id == project_id or (
        job.scope == "library" and job.job_type == "discovery.acquire" and job.payload.get("project_id") == project_id)))


def register_retry_validators(jobs, *, sections=None, planning=None, discovery=None, figures=None, final=None):
    """Fail stale retries before enqueueing; execution/publication checks remain authoritative."""
    def register(job_type, validate):
        jobs.register_retry_validator(job_type, lambda principal, job: validate(principal, job.project_id, job.payload))

    if sections is not None:
        def validate_sections(principal, project_id, payload):
            if not payload.get("_rewrite_scope"):
                ensure_sections_need_generation(sections, principal, project_id)
            sections.validate_generation_inputs(principal, project_id, payload)
        register("sections.generate", validate_sections)
    if discovery is not None:
        register("discovery.search", discovery.validate_search_inputs)
    if planning is not None:
        register("matrix.enrich", planning.validate_matrix_enrichment_inputs)
        def validate_blueprint(principal, project_id, payload):
            # A pending extraction is an explicit dependency; it is rebased by
            # the existing worker path, not treated as an unrelated Matrix edit.
            if not payload.get("await_matrix_job_id"):
                planning.validate_prepared_blueprint(principal, project_id, payload)
        register("planning.blueprint", validate_blueprint)
    if figures is not None:
        def validate_figures(principal, project_id, payload):
            for figure_id in payload.get("figure_ids") or []:
                figures.resolve_redraw_item(principal, project_id, payload, figure_id)
        register("figures.redraw", validate_figures)
    if final is not None:
        for job_type in ("final.conclusion", "final.overview", "final.export", "final.pdf", "final.build"):
            register(job_type, final.validate_task_inputs)


def confirm_discovery(discovery, planning, jobs, principal, project_id, revision):
    result = discovery.confirm(principal, project_id, revision)
    if planning is not None:
        job = queue_matrix_enrichment(planning, jobs, principal, project_id)
        result["matrix_analysis_job"] = job_payload(job)
    return result


def recommend_topic_outline(planning, jobs, principal, project_id, *, retry=False):
    """One submission policy for the stage page and conversation adapter."""
    from review_writer_api.errors import WorkflowValidationError
    inputs = planning.topic_recommendation_input(principal, project_id)
    if not inputs["papers"]:
        raise WorkflowValidationError("Select papers before requesting a topic outline.")
    state = planning.topic_recommendation_status(principal, project_id, inputs)
    if state["job"] and (not retry or state["status"] in {"queued", "running", "cancel_requested"}
                         or (state["status"] == "succeeded" and not state.get("additional_evidence_available"))):
        return state["job"]
    key = "topic-outline:" + inputs["input_fingerprint"]
    if retry:
        key += ":" + str(uuid.uuid4())
    try:
        return job_payload(jobs.submit(principal, scope="project", project_id=project_id,
            job_type="planning.topic-outline", operation_key="topic-outline", idempotency_key=key, payload=inputs))
    except WorkflowConflict:
        existing = jobs.repository.get_current_job(principal.user_id, scope="project", project_id=project_id,
            job_type="planning.topic-outline", operation_key="topic-outline")
        if not existing or existing.payload.get("input_fingerprint") != inputs["input_fingerprint"]:
            raise
        return job_payload(existing)


def acquire_discovery_candidate(discovery, jobs, principal, project_id, candidate_id, revision, request_key):
    """Discovery owns download, parsing and candidate refresh; no chat downloader."""
    from review_writer_api.security import Permission
    principal.require(Permission.PROJECT_WRITE)
    candidate = discovery.download_candidate(principal, project_id, candidate_id, revision)
    operation_key = f"project:{project_id}"
    def active():
        current = jobs.repository.get_current_job(principal.user_id, scope="library",
            job_type="discovery.acquire", operation_key=operation_key)
        if current is None or current.status not in {"queued", "running", "cancel_requested"}:
            return None
        if current.payload.get("discovery_revision") == revision and any(
            isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id
            for item in current.payload.get("candidates") or []):
            return current
        raise WorkflowConflict("Another paper is being downloaded. Wait for it to finish, then retry.")
    current = active()
    if current:
        return current
    try:
        return jobs.submit(principal, scope="library", project_id=None, job_type="discovery.acquire",
            idempotency_key=request_key, operation_key=operation_key,
            payload={"candidates": [candidate], "discovery_revision": revision, "project_id": project_id})
    except WorkflowConflict as exc:
        if not exc.details.get("current_job_id"):
            raise
        current = active()
        if current is None:
            raise
        return current


def generate_overview(final, jobs, principal, project_id, request, request_key):
    return jobs.submit(principal, scope="project", project_id=project_id, job_type="final.overview",
        idempotency_key=request_key, payload={**final.overview_payload(principal, project_id),
            "preview_only": True, "generation_instructions": request.instructions,
            "structure_references": [ref.model_dump() for ref in request.structure_references]})


def submit_blueprint(planning, jobs, principal, project_id, revision, request_key):
    prepared = planning.blueprint_job_payload(principal, project_id, revision=revision)
    try:
        return jobs.submit(principal, scope="project", project_id=project_id, job_type="planning.blueprint",
                           idempotency_key=request_key, operation_key="blueprint-planning", payload=prepared)
    except WorkflowConflict as conflict:
        existing_id = conflict.details.get("existing_job_id")
        if not existing_id:
            raise
        existing = jobs.status(principal, existing_id)
        if (existing.idempotency_key != request_key or existing.payload.get("blueprint_revision") != revision
                or existing.payload.get("draft_repair_input_artifacts") != prepared.get("draft_repair_input_artifacts")):
            raise
        return existing


def ensure_sections_need_generation(sections, principal, project_id):
    """Ordinary generation/retry must not replace an already complete current manuscript."""
    current = sections.get(principal, project_id)
    if isinstance(current, dict) and current.get("handoff", {}).get("current") is True:
        report = current.get("report") or {}
        if (report.get("current_task_count", 0) > 0
                and report.get("current_output_count") == report.get("current_task_count")
                and not report.get("pending_section_count")):
            raise WorkflowConflict(
                "Current section drafts are already complete. Refresh to view them; use explicit regeneration to replace them.",
                details={"current_outputs_available": True},
            )


def submit_sections(sections, jobs, principal, project_id, request_key, *, resume=False, restart=False):
    if not restart:
        ensure_sections_need_generation(sections, principal, project_id)
    payload = sections.generation_payload(principal, project_id, defer_evidence=True)
    def active_job():
        current = jobs.repository.get_current_job(principal.user_id, scope="project", project_id=project_id,
                                                  job_type="sections.generate")
        if current and current.status in {"queued", "running", "cancel_requested"} and current.payload.get("_rewrite_scope"):
            raise WorkflowConflict("A scoped chapter rewrite is active; it was not reused as whole-project generation.", details={"current_job_id": current.id})
        return current if current and current.status in {"queued", "running", "cancel_requested"} else None
    job = active_job()
    if job is None:
        try:
            source = section_resume_source(sections, jobs, principal, project_id, payload, request_key) if resume else None
            if source is not None:
                if source.idempotency_key == f"retry:{source.retry_of_job_id}:{request_key}":
                    return source  # an acknowledged or completed retry of this same operation
                job = jobs.retry_interrupted(principal, source.id, idempotency_key=request_key)
            else:
                job = jobs.submit(principal, scope="project", project_id=project_id, job_type="sections.generate",
                                  idempotency_key=request_key, payload=payload)
        except WorkflowConflict as exc:
            if not exc.details.get("current_job_id"):
                raise
            job = active_job()
            if job is None:
                raise
    return job


def section_resume_source(sections, jobs, principal, project_id, payload, request_key):
    """Read-only admission to the original retry path, never fabricate checkpoints."""
    from review_writer_api.submission_context import submission_model_source
    model_source = submission_model_source()
    admitted = jobs.repository.get_job(principal.user_id, model_source) if model_source else None
    snapshot = admitted.payload.get("_text_model_snapshot") if admitted else None
    records = jobs.repository.list_project_jobs(principal.user_id, project_id, job_type="sections.generate", limit=30)
    for source in records:
        if source.project_id != project_id or source.payload.get("_rewrite_scope"):
            continue
        if source.retry_of_job_id and source.idempotency_key == f"retry:{source.retry_of_job_id}:{request_key}":
            return source
        if source.status == "succeeded":
            break  # do not revive an older failure behind a completed generation
        checkpoint = (source.result or {}).get("section_checkpoint")
        if (source.status not in jobs.RETRYABLE_STATUSES or not isinstance(checkpoint, dict)
                or not (checkpoint.get("entries") or checkpoint.get("authoring_states"))):
            continue
        if any(source.payload.get(key) != payload.get(key) for key in (
                "source_blueprint_artifact_id", "source_matrix_artifact_id", "source_outline_artifact_id",
                "tasks", "expected_sections_revision")):
            continue
        previous_model = source.payload.get("_text_model_snapshot") or {}
        if snapshot and any(previous_model.get(key) != snapshot.get(key) for key in ("id", "model", "wire_api")):
            continue
        sections.validate_generation_inputs(principal, project_id, source.payload)
        return source
    return None


def assemble_draft(drafts, jobs, principal, project_id):
    from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT
    had_draft = bool(drafts.repository.list_artifacts(principal.user_id, project_id, DRAFT_MANUSCRIPT))
    result = drafts.assemble(principal, project_id)
    previous = drafts.repository.list_project_jobs(principal.user_id, project_id,
                                                  job_type="draft.optimize", idempotency_key="draft-initial-synthesis", limit=1)
    if not had_draft and not previous:
        payload = drafts.synthesis_payload(principal, project_id, "initial", initial=True)
        if payload["roles"]:
            try:
                job = jobs.submit(principal, scope="project", project_id=project_id, job_type="draft.optimize",
                                  operation_key="synthesis:initial", idempotency_key="draft-initial-synthesis", payload=payload)
                result["synthesis_job_id"] = job.id
            except WorkflowError:
                result["synthesis_warning"] = "正文已保存；摘要/结论尚未补齐，请在完整正文中按需生成。"
    return result


def save_manuscript_fields(drafts, principal, project_id, *, revision, title, keywords, expected_draft_artifact_id=None, changed_fields=None):
    from review_writer_api.errors import WorkflowValidationError
    from review_writer_core.draft_composition import replace_manuscript_fields
    from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT
    if not isinstance(title, str) or not title.strip():
        raise WorkflowValidationError("Manuscript title cannot be blank.")
    text, artifact = drafts._read_text(principal, project_id, DRAFT_MANUSCRIPT)
    if expected_draft_artifact_id and artifact.id != expected_draft_artifact_id:
        raise WorkflowConflict("Draft changed before the manuscript fields were edited.")
    return drafts.save_text(principal, project_id, text=replace_manuscript_fields(text, title, keywords),
                            revision=revision, expected_draft_artifact_id=artifact.id,
                            operation="manuscript-title-edit" if changed_fields == {"title"} else "manuscript-fields-edit")
