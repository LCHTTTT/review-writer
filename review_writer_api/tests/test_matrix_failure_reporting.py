"""Regression for 1013: a pending checkpoint is not a finished fact result."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from review_writer_api.domain_services.planning import _matrix_enrichment_recovery
from review_writer_api.errors import ModelTaskFailed
from review_writer_api.job_handlers.stage_execution import wait_for_delegated_model
from review_writer_api.job_service import JobYieldRequested
from review_writer_api.model_gateway import ModelGatewayService


def job(checkpoint, *, total=1, code="STATE_CONFLICT", message="Matrix changed before publication."):
    return SimpleNamespace(result={"matrix_enrichment_checkpoint": checkpoint}, progress_total=total,
                           status="failed", error_code=code, error_message=message)


def checkpoint(status="complete"):
    return {"source_matrix_artifact_id": "matrix", "completed_papers": ["P1"],
            "entries": {"P1": {"result": {"status": status, "facts": []}}}}


@pytest.mark.parametrize("saved", [{}, {"source_matrix_artifact_id": "matrix", "completed_papers": [],
                                       "entries": {"P1": {"result": {}, "agent_state": {"waiting_model_job_ids": ["child"]}}}}])
def test_empty_or_pending_checkpoint_never_claims_finished_results(saved):
    assert _matrix_enrichment_recovery(job(saved), "matrix") == {
        "completed_paper_ids": [], "publication_pending": False}


def test_completed_ids_must_have_successful_results():
    assert _matrix_enrichment_recovery(job(checkpoint("failed")), "matrix") == {
        "completed_paper_ids": [], "publication_pending": False}


def test_partially_finished_batch_retains_progress_without_claiming_a_publish_failure():
    assert _matrix_enrichment_recovery(job(checkpoint(), total=2), "matrix") == {
        "completed_paper_ids": ["P1"], "publication_pending": False}


def test_complete_current_results_identify_actual_publication_conflict():
    assert _matrix_enrichment_recovery(job(checkpoint()), "matrix") == {
        "completed_paper_ids": ["P1"], "publication_pending": True}
    assert not _matrix_enrichment_recovery(job(checkpoint()), "new-matrix")["publication_pending"]


def test_legacy_delegated_failure_is_not_a_publication_conflict():
    saved = job(checkpoint(), message="The delegated model task did not finish. No model call was replayed automatically.")
    assert not _matrix_enrichment_recovery(saved, "matrix")["publication_pending"]
    saved.error_message = "Model provider returned an empty response."
    saved.error_code = "PROVIDER_EMPTY_RESPONSE"
    assert not _matrix_enrichment_recovery(saved, "matrix")["publication_pending"]


def test_legacy_checkpoint_key_uses_the_same_completion_rules():
    saved = job({})
    saved.result = {"section_checkpoint": checkpoint()}
    assert _matrix_enrichment_recovery(saved, "matrix")["completed_paper_ids"] == ["P1"]


@pytest.mark.parametrize("code,message", [
    ("PROVIDER_EMPTY_RESPONSE", "模型仅返回推理过程，没有可用答案。"),
    ("SCIENTIFIC_RUN_FAILED", "The original model request outcome is unknown."),
    ("", ""),
])
def test_parent_keeps_child_failure_instead_of_reporting_a_state_change(code, message):
    child = SimpleNamespace(project_id="project", job_type="model.dispatch", status="failed",
                            error_code=code, error_message=message)
    repository = Mock()
    repository.get_job.return_value = child
    context = SimpleNamespace(user_id="owner", project_id="project", repository=repository)
    with pytest.raises(ModelTaskFailed) as failed:
        wait_for_delegated_model(context, {"waiting_model_job_ids": ["child"]})
    assert failed.value.code == (code or "MODEL_TASK_FAILED")
    assert failed.value.code != "STATE_CONFLICT"
    assert str(failed.value) == message if message else "模型任务未完成" in str(failed.value)
    repository.get_job.assert_called_once_with("owner", "child")


def test_running_model_still_yields_without_changing_task_flow():
    child = SimpleNamespace(project_id="project", job_type="model.dispatch", status="running")
    context = SimpleNamespace(user_id="owner", project_id="project",
                              repository=SimpleNamespace(get_job=lambda *args: child))
    with pytest.raises(JobYieldRequested):
        wait_for_delegated_model(context, {"waiting_model_job_ids": ["child"]})


def test_parallel_result_reader_also_receives_original_child_error():
    child = SimpleNamespace(id="child", status="failed", result={},
                            error_message="模型仅返回推理过程，没有可用答案。")
    snapshot = ModelGatewayService._delegated_text_snapshot(child)
    assert snapshot["error"] == child.error_message
    assert snapshot["output_text"] == ""
