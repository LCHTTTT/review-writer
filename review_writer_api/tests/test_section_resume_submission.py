"""Offline continuation admission: no providers, no real jobs or users."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from review_writer_api.application_operations import submit_sections
from review_writer_api.domain_services.conversation_authorization import authorize_action
from review_writer_api.errors import WorkflowValidationError
from review_writer_api.submission_context import bind_submission_model_source


def fixture(status="failed"):
    payload = {"source_blueprint_artifact_id": "blueprint", "source_matrix_artifact_id": "matrix",
               "source_outline_artifact_id": "outline", "tasks": [{"section_id": "S04"}], "expected_sections_revision": 0}
    source = SimpleNamespace(id="source", project_id="project", status=status, job_type="sections.generate",
        payload=deepcopy(payload), result={"section_checkpoint": {"entries": {"S01": {"output": "Completed prose"}}}},
        idempotency_key="old-request", retry_of_job_id=None)
    principal = SimpleNamespace(user_id="user")
    sections, jobs = Mock(), Mock()
    sections.get.return_value = {"handoff": {"current": False}}
    jobs.RETRYABLE_STATUSES = {"failed", "cancelled", "interrupted"}
    jobs.repository.get_current_job.return_value = None
    jobs.repository.list_project_jobs.return_value = [source]
    sections.generation_payload.return_value = deepcopy(payload)
    return principal, sections, jobs, source


@pytest.mark.parametrize("status", ["failed", "interrupted", "cancelled"])
def test_continuation_uses_linked_retry_without_rebuilding_checkpoint(status):
    principal, sections, jobs, source = fixture(status)
    before = deepcopy(source.result)
    result = submit_sections(sections, jobs, principal, "project", "operation", resume=True)
    assert result is jobs.retry_interrupted.return_value
    jobs.retry_interrupted.assert_called_once_with(principal, "source", idempotency_key="operation")
    jobs.submit.assert_not_called()
    assert source.result == before


def test_fresh_page_generation_keeps_existing_behavior():
    principal, sections, jobs, _ = fixture()
    submit_sections(sections, jobs, principal, "project", "operation")
    jobs.submit.assert_called_once()
    jobs.retry_interrupted.assert_not_called()


@pytest.mark.parametrize("field", ["source_blueprint_artifact_id", "source_matrix_artifact_id", "source_outline_artifact_id", "tasks", "expected_sections_revision"])
def test_changed_input_does_not_retry_stale_snapshot(field):
    principal, sections, jobs, source = fixture()
    source.payload[field] = "changed"
    submit_sections(sections, jobs, principal, "project", "operation", resume=True)
    jobs.submit.assert_called_once()
    jobs.retry_interrupted.assert_not_called()


def test_existing_active_job_is_reused_without_retry_or_submission():
    principal, sections, jobs, source = fixture("running")
    jobs.repository.get_current_job.return_value = source
    assert submit_sections(sections, jobs, principal, "project", "operation", resume=True) is source
    jobs.submit.assert_not_called()
    jobs.retry_interrupted.assert_not_called()


@pytest.mark.parametrize("case", ["foreign", "scoped", "no_checkpoint", "succeeded", "changed_model"])
def test_irrelevant_or_completed_sources_cannot_be_revived(case):
    principal, sections, jobs, source = fixture()
    if case == "foreign":
        source.project_id = "other"
    elif case == "scoped":
        source.payload["_rewrite_scope"] = {"section_id": "S04"}
    elif case == "no_checkpoint":
        source.result = {}
    elif case == "succeeded":
        source.status = "succeeded"
    else:
        jobs.repository.get_job.return_value = SimpleNamespace(payload={"_text_model_snapshot": {"id": "new"}})
    with bind_submission_model_source("turn" if case == "changed_model" else None):
        submit_sections(sections, jobs, principal, "project", "operation", resume=True)
    jobs.submit.assert_called_once()
    jobs.retry_interrupted.assert_not_called()


def test_transport_and_price_refresh_do_not_discard_completed_chapters():
    principal, sections, jobs, source = fixture()
    source.payload["_text_model_snapshot"] = {"id": "same", "model": "same", "wire_api": "responses", "input_price": "old"}
    jobs.repository.get_job.return_value = SimpleNamespace(payload={"_text_model_snapshot": {
        "id": "same", "model": "same", "wire_api": "responses", "input_price": "new", "connection_revision": 2}})
    with bind_submission_model_source("turn"):
        submit_sections(sections, jobs, principal, "project", "operation", resume=True)
    jobs.retry_interrupted.assert_called_once()
    jobs.submit.assert_not_called()


def test_same_continuation_submission_returns_existing_linked_retry():
    principal, sections, jobs, source = fixture("succeeded")
    source.retry_of_job_id = "previous"
    source.idempotency_key = "retry:previous:operation"
    assert submit_sections(sections, jobs, principal, "project", "operation", resume=True) is source
    jobs.submit.assert_not_called()
    jobs.retry_interrupted.assert_not_called()


def test_restart_needs_explicit_request_and_is_not_automatic_completion():
    for message, automatic in [("继续到终稿", True), ("生成所有章节", False), ("不要重新生成章节", False)]:
        with pytest.raises(WorkflowValidationError):
            authorize_action(message, message, "sections.generate", {"restart": True}, automatic=automatic, stop_at="final")
    authorize_action("重新生成所有章节", "重新生成所有章节", "sections.generate", {"restart": True})


@pytest.mark.parametrize("resume", [False, True])
def test_completed_current_outputs_block_stale_page_submission_before_model_admission(resume):
    from review_writer_api.errors import WorkflowConflict
    principal, sections, jobs, _ = fixture()
    sections.get.return_value = {"handoff": {"current": True}, "report": {
        "current_task_count": 28, "current_output_count": 28, "pending_section_count": 0}}
    with pytest.raises(WorkflowConflict) as error:
        submit_sections(sections, jobs, principal, "project", "operation", resume=resume)
    assert error.value.details["current_outputs_available"] is True
    sections.generation_payload.assert_not_called()
    jobs.submit.assert_not_called()
    jobs.retry_interrupted.assert_not_called()


def test_explicit_restart_can_replace_complete_outputs():
    principal, sections, jobs, _ = fixture()
    sections.get.return_value = {"handoff": {"current": True}, "report": {
        "current_task_count": 28, "current_output_count": 28, "pending_section_count": 0}}
    submit_sections(sections, jobs, principal, "project", "operation", restart=True)
    jobs.submit.assert_called_once()


@pytest.mark.parametrize("current,outputs,pending", [(False,28,0), (True,27,1), (True,28,1)])
def test_stale_or_pending_outputs_remain_generatable(current, outputs, pending):
    principal, sections, jobs, _ = fixture()
    sections.get.return_value = {"handoff": {"current": current}, "report": {
        "current_task_count": 28, "current_output_count": outputs, "pending_section_count": pending}}
    submit_sections(sections, jobs, principal, "project", "operation")
    jobs.submit.assert_called_once()
