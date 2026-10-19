"""Chapter reading uses isolated data; no provider calls or real project writes."""
import json
from unittest.mock import patch

import pytest

from review_writer_api.domain_services.conversation_reading import section_view
from review_writer_api.errors import WorkflowNotFound, WorkflowValidationError
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_core.workflow import artifacts as names


def sample():
    return json.dumps({"sections": [
        {"section_id": "S01", "heading": "Introduction", "paragraphs": [
            {"paragraph_id": "S01-p1", "text": "Actual prose [1].", "cited_paper_ids": ["P1"],
             "claims": [{"claim_id": "C1", "source_verification": {"status": "supported"}, "evidence_refs": ["P1:chunk1"]}],
             "diagnostics": "internal-audit-" * 45000},
            {"paragraph_id": "S01-p2", "text": "Second paragraph."}],
         "claims": [{"claim_id": "C2", "paragraph_id": "S01-p2", "evidence_refs": ["P2:chunk2"]}]},
        {"section_id": "S02", "heading": "Methods", "paragraphs": [
            {"paragraph_id": "S02-p1", "text": "Methods prose."}]}]}, ensure_ascii=False)


def test_large_json_projects_only_prose_and_retains_ids_and_citations():
    raw = sample()
    assert len(raw) > 540000
    prose, details = section_view(raw, section_id="S01")
    assert len(prose) < 200
    assert "Actual prose [1]." in prose and "S01-p1" in prose and "P1" in prose
    assert "Second paragraph." in prose and "Methods prose" not in prose
    assert "internal-audit" not in prose and "source_verification" not in prose
    assert details["scope"] == "sections" and details["view"] == "prose"


def test_directory_and_targeted_evidence_support_both_saved_claim_layouts():
    directory, details = section_view(sample())
    assert [s["section_id"] for s in json.loads(directory)] == ["S01", "S02"]
    assert details["view"] == "directory" and "Actual prose" not in directory
    value, _ = section_view(sample(), section_id="S01", paragraph_id="S01-p1", view="evidence")
    assert len(value) < 1000 and "internal-audit" not in value
    assert json.loads(value)["paragraph"]["claims"][0]["evidence_refs"] == ["P1:chunk1"]
    value, _ = section_view(sample(), section_id="S01", paragraph_id="S01-p2", view="evidence")
    assert json.loads(value)["section_claims"][0]["evidence_refs"] == ["P2:chunk2"]


def test_missing_prose_and_invalid_scope_do_not_fabricate_content():
    prose, _ = section_view(json.dumps({"sections": [{"section_id": "old", "draft_md": "## Legacy\nSaved body."}]}), section_id="old")
    assert "Saved body." in prose
    for options in ({"section_id": "missing"}, {"section_id": "S01", "paragraph_id": "S02-p1"}):
        with pytest.raises(WorkflowNotFound):
            section_view(sample(), **options)
    with pytest.raises(WorkflowValidationError):
        section_view(sample(), section_id="*", view="evidence")
    with pytest.raises(WorkflowValidationError):
        section_view("broken JSON", section_id="S01")


class SectionReadingTests(NativeFigureApiTestCase):
    def test_matrix_view_language_aliases_preserve_saved_data_and_permissions(self):
        from fastapi.testclient import TestClient
        repository = self.app.state.workflow_repository
        artifact = repository.get_current_artifact(self.first.user_id, self.project_id, names.MATRIX)
        before = repository.get_stage_state(self.first.user_id, self.project_id, "matrix")
        endpoint = f"/api/v1/projects/{self.project_id}/conversation/artifacts/{artifact.id}"
        with TestClient(self.app) as client:
            zh = client.get(endpoint, params={"view": "readable", "language": "zh"})
            legacy = client.get(endpoint, params={"view": "readable", "language": "zh-CN"})
            en = client.get(endpoint, params={"view": "readable", "language": "en"})
            assert zh.status_code == legacy.status_code == en.status_code == 200
            assert zh.json()["text"] == legacy.json()["text"]
            assert "文献条目" in zh.json()["text"] and "Paper entries" in en.json()["text"]
            assert "Copper paper" in zh.json()["text"]
            assert client.get(endpoint, params={"language": "fr"}).status_code == 422
            raw = client.get(endpoint, params={"view": "raw", "language": "zh-CN"})
            assert json.loads(raw.json()["text"])["rows"][0]["paper_id"] == "P001"
            self.current = self.second
            assert client.get(endpoint, params={"view": "readable", "language": "zh-CN"}).status_code == 404
        assert repository.get_current_artifact(self.first.user_id, self.project_id, names.MATRIX).id == artifact.id
        assert repository.get_stage_state(self.first.user_id, self.project_id, "matrix") == before

    def test_no_saved_body_is_an_empty_state_and_invalid_requests_are_rejected(self):
        service = self.app.state.conversation_service
        with patch.object(service.repository, "get_current_artifact", return_value=None):
            value = service.execute_read(self.first, self.project_id, "read_section", {})
            assert value["available"] is False
        with pytest.raises(WorkflowValidationError):
            service.execute_read(self.first, self.project_id, "read_artifact", {})

    def test_read_tools_use_prose_but_original_artifact_contract_is_unchanged(self):
        service = self.app.state.conversation_service
        repository = self.app.state.workflow_repository
        artifact = repository.get_current_artifact(self.first.user_id, self.project_id, names.SECTION_DRAFTS)
        state_before = repository.get_stage_state(self.first.user_id, self.project_id, "sections")
        raw = service.read_artifact(self.first, self.project_id, artifact.id)
        assert json.loads(raw["text"])["sections"][0]["section_id"] == "S1"
        prose = service.execute_read(self.first, self.project_id, "read_artifact", {"artifact_id": artifact.id})
        assert "Grounded paragraph." in prose["text"] and '"sections"' not in prose["text"]
        assert prose["view"] == "prose" and prose["current"]
        assert service.execute_read(self.first, self.project_id, "read_artifact", {"artifact_id": artifact.id, "view": "raw"}) == raw
        directory = service.execute_read(self.first, self.project_id, "read_section", {})
        assert json.loads(directory["text"])[0]["section_id"] == "S1"
        selected = service.execute_read(self.first, self.project_id, "read_section", {"section_id": "S1", "paragraph_id": "S1-p2"})
        assert "no usable source figure" in selected["text"] and "Grounded paragraph" not in selected["text"]
        first = service.read_section(self.first, self.project_id, section_id="S1", limit=10)
        rest = service.read_section(self.first, self.project_id, section_id="S1", offset=first["next_offset"])
        full = service.read_section(self.first, self.project_id, section_id="S1")
        assert first["text"] + rest["text"] == full["text"] and full["complete"]
        assert repository.get_current_artifact(self.first.user_id, self.project_id, names.SECTION_DRAFTS).id == artifact.id
        assert repository.get_stage_state(self.first.user_id, self.project_id, "sections") == state_before
        with pytest.raises(WorkflowNotFound):
            service.read_section(self.second, self.project_id, artifact_id=artifact.id, section_id="S1")
        with pytest.raises(WorkflowValidationError):
            service.read_section(self.first, self.project_id, section_id="S1", limit=20001)
        with pytest.raises(WorkflowValidationError):
            service.read_section(self.first, self.project_id, section_id="S1", offset=True)
