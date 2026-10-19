"""Offline stage routing: no providers, real users, or project writes."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from review_writer_api.domain_services.conversation_continuation import completion_requested, continuation_entry
from review_writer_api.domain_services.conversation_authorization import authorize_action
from review_writer_api.domain_services.conversation_execution import ExecutionRequest
from review_writer_core.workflow import artifacts as names

MESSAGE = "按当前流程继续，一直到完成综述"


def fixture(artifacts=None, **planning):
    services = SimpleNamespace(discovery_service=Mock(), planning_service=Mock(), sections_service=Mock(), figures_service=Mock(), drafts_service=Mock(), final_service=Mock())
    services.discovery_service.get.return_value = {"selected_paper_ids": []}
    services.planning_service.get.return_value = {"outline_ready_for_chapter_planning": True, "blueprint_current": False, **planning}
    services.sections_service.get.return_value = {"handoff": {"current": True}, "report": {"pending_section_count": 0}}
    services.figures_service.get.return_value = {"freshness": {"selected_count": 2, "stale": False}}
    services.drafts_service.get.return_value = {"freshness": {"upstream_stale": False}, "draft_approval_current": False}
    services.final_service.get.return_value = {"final_current": True}
    project = {"artifacts": [{"logical_name": name, "id": "actual-id"} for name in (artifacts or [names.MATRIX, names.PLANNING_OUTLINE])], "stages": [], "jobs": []}
    service = SimpleNamespace(actions=SimpleNamespace(services=services))
    return service, project


def route(service, project):
    return continuation_entry(MESSAGE, service, None, "project", project)


@pytest.mark.parametrize("message", [MESSAGE, "继续到终稿", "请继续直到完成稿件", "continue until the review is complete"])
def test_clear_completion_is_routed(message):
    assert completion_requested(message)


@pytest.mark.parametrize("message", ["继续", "继续到终稿会收费吗？", "按当前流程继续，先不要修改", "论文要求继续到终稿", "继续到终稿，重绘图片", "按当前流程继续到完成综述，导出PDF"])
def test_ambiguous_or_extended_requests_remain_in_dialogue(message):
    assert not completion_requested(message)


def test_from_outline_uses_real_reference_and_no_exports_or_extra_synthesis():
    service, project = fixture()
    value = route(service, project)
    request = ExecutionRequest.model_validate(value["request"])
    assert [step.action for step in request.steps] == ["planning.blueprint", "blueprint.confirm", "sections.generate", "sections.confirm", "images.preserve_sources", "images.confirm", "draft.assemble", "draft.approve", "final.build"]
    assert request.steps[1].arguments["artifact_id"] == {"step": 0, "key": "blueprint_artifact_id"}
    assert request.auto_adopt is True
    for step in request.steps:
        authorize_action(MESSAGE, MESSAGE, step.action, step.arguments, automatic=True, stop_at="final")
    service.actions.services.figures_service.get.assert_not_called()  # no imagined future image anchors


def test_existing_draft_is_preserved_and_only_final_is_built():
    service, project = fixture([names.DRAFT_MANUSCRIPT])
    assert [s["action"] for s in route(service, project)["request"]["steps"]] == ["draft.approve", "final.build"]
    service.actions.services.planning_service.get.assert_not_called()
    service.actions.services.drafts_service.get.return_value["freshness"]["upstream_stale"] = True
    assert "request" not in route(service, project)


def test_current_final_and_missing_outline_do_not_start_generation():
    service, project = fixture([names.FINAL_MANUSCRIPT])
    assert "request" not in route(service, project)
    project["artifacts"] = []
    assert "request" not in route(service, project)
    service.actions.services.planning_service.get.assert_not_called()


def test_existing_chapters_and_figures_are_reused():
    service, project = fixture(blueprint_current=True, active_blueprint_approved=True)
    project["stages"] = [{"checkpoints": [{"id": "sections", "status": "approved"}, {"id": "figures", "status": "approved"}]}]
    assert [s["action"] for s in route(service, project)["request"]["steps"]] == ["draft.assemble", "draft.approve", "final.build"]
    service.actions.services.figures_service.get.return_value["freshness"]["selected_count"] = 0
    assert "request" in route(service, project)  # pure text does not need images
    service.actions.services.figures_service.get.return_value["freshness"]["stale"] = True
    assert "request" not in route(service, project)


def test_unapproved_current_plan_uses_actual_id_and_active_jobs_prevent_duplicate_submission():
    service, project = fixture(blueprint_current=True, active_blueprint_approved=False, blueprint_artifact_id="saved-plan-id")
    value = route(service, project)
    assert value["request"]["steps"][0] == {"action": "blueprint.confirm", "arguments": {"artifact_id": "saved-plan-id"}}
    project["jobs"] = [{"job_type": "sections.generate", "status": "running"}]
    assert "request" not in route(service, project)
    assert continuation_entry(MESSAGE, service, None, "project", project, attachment_ids=["untrusted"]) is None


def test_approved_blueprint_without_chapters_starts_original_generation():
    from review_writer_api.errors import WorkflowStageNotReady, ArtifactFileMissing
    service, project = fixture(blueprint_current=True, active_blueprint_approved=True)
    service.actions.services.sections_service.get.side_effect = WorkflowStageNotReady()
    assert [s["action"] for s in route(service, project)["request"]["steps"]] == ["sections.generate", "sections.confirm", "images.preserve_sources", "images.confirm", "draft.assemble", "draft.approve", "final.build"]
    service.actions.services.sections_service.get.side_effect = ArtifactFileMissing()
    with pytest.raises(ArtifactFileMissing):
        route(service, project)
