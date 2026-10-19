import json
import pytest
from fastapi.testclient import TestClient
from review_writer_core.workflow import artifacts as names

@pytest.fixture
def fixture():
    from review_writer_api.tests.test_sections_v1 import SectionsV1Tests
    value = SectionsV1Tests()
    value.setUp()
    try:
        yield value
    finally:
        value.tearDown()


def read(f, name):
    artifact = f.app.state.workflow_repository.get_current_artifact(f.first.user_id, f.project_id, name)
    return json.loads(f.app.state.artifact_service.resolve_owned_artifact(f.first.user_id, artifact.id).path.read_text()), artifact


def generate(f, client):
    result = client.post(f"/api/v1/projects/{f.project_id}/sections/jobs", headers={"Idempotency-Key": "original-generation"}, json={})
    assert result.status_code == 202, result.text
    job = f.wait_job(client, result.json()["id"])
    assert job["status"] == "succeeded", job
    return read(f, names.SECTION_DRAFTS)


def test_scoped_writer_generates_candidate_only_and_adopts_original_upstream_not_draft(fixture):
    f = fixture
    with TestClient(f.app) as client:
        index, artifact = generate(f, client)
        assert len(index["sections"]) > 1
        sid = index["sections"][0]["section_id"]
        response = client.post(f"/api/v1/projects/{f.project_id}/sections/{sid}/rewrite-jobs", headers={"Idempotency-Key": "scoped-rewrite"}, json={"message": "Improve the whole chapter's organization while retaining source-supported conclusions.", "expected_sections_artifact_id": artifact.id})
        assert response.status_code == 202, response.text
        queued = f.app.state.workflow_repository.get_job(f.first.user_id, response.json()["id"])
        assert [task["section_id"] for task in queued.payload["tasks"]] == [sid]
        assert queued.payload["evidence_preparation_pending"]  # provider work stays behind admission
        job = f.wait_job(client, response.json()["id"])
        assert job["status"] == "succeeded", job
        assert read(f, names.SECTION_DRAFTS)[1].id == artifact.id
        assert f.writer_calls == 2
        candidate = job["result"]["candidate_artifact_id"]
        f.current = f.second
        foreign = client.post(f"/api/v1/projects/{f.project_id}/sections/rewrite-candidates/{candidate}/accept")
        assert foreign.status_code in (403, 404)
        f.current = f.first
        result = client.post(f"/api/v1/projects/{f.project_id}/sections/rewrite-candidates/{candidate}/accept")
        assert result.status_code == 200, result.text
        assert result.json()["rewritten_section_ids"] == [sid]
        assert result.json()["draft_unchanged"]
        changed, _ = read(f, names.SECTION_DRAFTS)
        assert [row for row in changed["sections"] if row["section_id"] != sid] == [row for row in index["sections"] if row["section_id"] != sid]
        assert f.app.state.workflow_repository.get_current_artifact(f.first.user_id, f.project_id, names.DRAFT_MANUSCRIPT) is None
        replay = client.post(f"/api/v1/projects/{f.project_id}/sections/rewrite-candidates/{candidate}/accept")
        assert replay.status_code == 200
        assert replay.json()["already_adopted"]
        assert replay.json()["section_index_artifact_id"] == result.json()["section_index_artifact_id"]
        lost_ack = client.post(f"/api/v1/projects/{f.project_id}/sections/{sid}/rewrite-jobs", headers={"Idempotency-Key": "scoped-rewrite"}, json={"message": "Improve the whole chapter's organization while retaining source-supported conclusions.", "expected_sections_artifact_id": artifact.id})
        assert lost_ack.status_code == 202, lost_ack.text
        assert lost_ack.json()["id"] == job["id"]
        assert f.writer_calls == 2


def test_source_validation_failure_does_not_publish_or_replace_saved_chapters(fixture):
    f = fixture
    with TestClient(f.app) as client:
        index, artifact = generate(f, client)
        sid = index["sections"][0]["section_id"]
        f.evidence_outcome = "forged_pending"
        response = client.post(f"/api/v1/projects/{f.project_id}/sections/{sid}/rewrite-jobs", headers={"Idempotency-Key": "invalid-candidate"}, json={"message": "Revise this whole chapter.", "expected_sections_artifact_id": artifact.id})
        assert response.status_code == 202, response.text
        job = f.wait_job(client, response.json()["id"])
        assert job["status"] == "failed", job
        assert not (job.get("result") or {}).get("candidate_artifact_id")
        assert read(f, names.SECTION_DRAFTS)[1].id == artifact.id


def test_current_message_authorization_requires_upstream_scope_and_exact_candidate():
    from review_writer_api.domain_services.conversation_authorization import authorize_action
    from review_writer_api.errors import WorkflowValidationError
    message = "重写上游 S1 整章，改善组织"
    authorize_action(message, message, "sections.rewrite", {"section_id": "S1", "message": "改善组织", "artifact_id": "base"})
    for message in ("重写初稿 S1 整章，改善组织", "重写上游 S1 这一段，改善组织"):
        with pytest.raises(WorkflowValidationError):
            authorize_action(message, message, "sections.rewrite", {"section_id": "S1", "message": "改善组织", "artifact_id": "base"})
    message = "采用上游章节改写候选 candidate-1"
    authorize_action(message, message, "sections.accept_rewrite", {"artifact_id": "candidate-1"})
    with pytest.raises(WorkflowValidationError):
        authorize_action("继续", "继续", "sections.accept_rewrite", {"artifact_id": "candidate-1"})


def test_partial_request_cannot_expand_to_a_whole_chapter_or_cross_accounts(fixture):
    f = fixture
    with TestClient(f.app) as client:
        index, artifact = generate(f, client)
        sid = index["sections"][0]["section_id"]
        url = f"/api/v1/projects/{f.project_id}/sections/{sid}/rewrite-jobs"
        calls = f.writer_calls
        response = client.post(url, headers={"Idempotency-Key": "wrong-scope"}, json={"message": "Rewrite only this paragraph", "expected_sections_artifact_id": artifact.id})
        assert response.status_code == 422, response.text
        assert f.writer_calls == calls
        f.current = f.second
        response = client.post(url, headers={"Idempotency-Key": "other-owner"}, json={"message": "Rewrite the whole chapter", "expected_sections_artifact_id": artifact.id})
        assert response.status_code in (403, 404)
        assert read(f, names.SECTION_DRAFTS)[1].id == artifact.id
