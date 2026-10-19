from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from review_writer_api.domain_services.planning_field_edits import patch_blueprint_fields
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError


def service():
    document = {"sections": [{"section_id": "S1", "title": "Old", "review_problem": "Compare systems", "primary_papers": ["P1"], "planning_fact_context": {"fact_ids": ["F1"]}}], "source_matrix_artifact_id": "matrix", "source_outline_artifact_id": "outline", "resolved_outline_md": "## 1. Old\nSection role: body\n<!-- section_id: S1 -->\nNotes: Keep this custom note.\n"}
    instance = SimpleNamespace(_write_lock=nullcontext(), _owned_project=Mock(), _read_json=Mock(return_value=(document, SimpleNamespace(id="base"))),
        repository=SimpleNamespace(get_stage_state=Mock(return_value=SimpleNamespace(revision=3))),
        publish_blueprint_candidate=Mock(return_value={"blueprint_artifact_id": "candidate"}), confirm_blueprint=Mock(return_value={"status": "approved"}))
    return instance, document


def test_patch_retains_ids_allocations_and_scientific_fields_and_reuses_native_adoption():
    planning, doc = service()
    original = deepcopy(doc)
    result = patch_blueprint_fields(planning, SimpleNamespace(require=Mock(), user_id="user"), "project", section_id="S1", fields={"title": "New"}, expected_artifact_id="base")
    proposed = planning.publish_blueprint_candidate.call_args.args[2]["section_blueprint"]
    assert proposed["sections"][0] == {**original["sections"][0], "title": "New"}
    assert proposed["resolved_outline_md"] == original["resolved_outline_md"].replace("## 1. Old", "## 1. New")
    assert doc == original
    planning.confirm_blueprint.assert_called_once()
    assert result["saved_fields"] == {"title": "New"}


def test_unchanged_save_neither_publishes_nor_invalidates():
    planning, _ = service()
    result = patch_blueprint_fields(planning, SimpleNamespace(require=Mock(), user_id="user"), "project", section_id="S1", fields={"title": "Old"}, expected_artifact_id="base")
    assert result["unchanged"]
    planning.publish_blueprint_candidate.assert_not_called()
    planning.confirm_blueprint.assert_not_called()


def test_new_question_requires_replanning_and_cannot_approve_old_arguments():
    planning, _ = service()
    with pytest.raises(WorkflowValidationError):
        patch_blueprint_fields(planning, SimpleNamespace(require=Mock(), user_id="user"), "project", section_id="S1", fields={"review_problem": "A different question"}, expected_artifact_id="base")
    planning.publish_blueprint_candidate.assert_not_called()
    patch_blueprint_fields(planning, SimpleNamespace(require=Mock(), user_id="user"), "project", section_id="S1", fields={"review_problem": "A different question"}, expected_artifact_id="base", save=False)
    proposed = planning.publish_blueprint_candidate.call_args.args[2]["section_blueprint"]
    assert proposed["academic_planning"]["status"] == "incomplete"
    assert proposed["sections"][0]["planning_status"] == "pending"
    planning.confirm_blueprint.assert_not_called()


def test_stale_or_evidence_field_does_not_publish():
    planning, _ = service()
    for expected, fields, error in [("stale", {"title": "New"}, WorkflowConflict), ("base", {"planning_fact_context": "invented"}, WorkflowValidationError)]:
        with pytest.raises(error):
            patch_blueprint_fields(planning, SimpleNamespace(require=Mock(), user_id="user"), "project", section_id="S1", fields=fields, expected_artifact_id=expected)
    planning.publish_blueprint_candidate.assert_not_called()
