"""Actual offline candidate publication/adoption, not mocked business services."""
import json
import pytest
from fastapi.testclient import TestClient
from review_writer_api.tests.test_planning_v1 import seed_verified_matrix
from sqlalchemy import select, func
from review_writer_api.workflow_models import WorkflowJob
from review_writer_core.workflow import artifacts as names

@pytest.fixture
def planning_fixture():
    from review_writer_api.tests.test_planning_v1 import PlanningV1Tests
    fixture = PlanningV1Tests("test_blueprint_confirmation_advances_to_sections")
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def test_literal_heading_patch_preserves_other_sections_and_adopts_matching_outline(planning_fixture):
    f = planning_fixture
    service, repo = f.app.state.planning_service, f.app.state.workflow_repository
    seed_verified_matrix(service, f.first, f.project_id)
    with TestClient(f.app) as client:
        f.choose_outline(client, "reaction")
        generated = f.generate_candidate(client, json={"revision": 0}, headers=f.headers()).json()
        candidate_id = generated["blueprint_artifact_id"]
        adopted = client.post(f"/api/v1/projects/{f.project_id}/planning/blueprint/confirm", json={"revision": 0, "artifact_id": candidate_id}, headers=f.headers())
        assert adopted.status_code == 200, adopted.text
        before = generated["section_blueprint"]
        section = next(row for row in before["sections"] if row.get("section_role") == "body")
        path = f"/api/v1/projects/{f.project_id}/planning/blueprint/sections/{section['section_id']}/fields"
        def job_count():
            with f.sessions() as session:
                return session.scalar(select(func.count()).select_from(WorkflowJob))
        metadata_requests = job_count()
        response = client.patch(path, json={"expected_artifact_id": candidate_id, "fields": {"title": "Human revised title"}})
        assert response.status_code == 200, response.text
        value = response.json()
        after = value["section_blueprint"]
        changed = next(row for row in after["sections"] if row["section_id"] == section["section_id"])
        assert changed == {**section, "title": "Human revised title"}
        assert [row for row in after["sections"] if row["section_id"] != section["section_id"]] == [row for row in before["sections"] if row["section_id"] != section["section_id"]]
        active = repo.get_current_artifact(f.first.user_id, f.project_id, names.BLUEPRINT)
        assert active.id == value["blueprint_artifact_id"]
        outline = f.app.state.artifact_service.resolve_owned_artifact(f.first.user_id, after["source_outline_artifact_id"])
        assert json.loads(outline.path.read_text())["outline_md"] == after["resolved_outline_md"]
        assert "Human revised title" in after["resolved_outline_md"]
        assert job_count() == metadata_requests
        stale = client.patch(path, json={"expected_artifact_id": candidate_id, "fields": {"title": "Stale overwrite"}})
        assert stale.status_code == 409
        f.current = f.second
        forbidden = client.patch(path, json={"expected_artifact_id": active.id, "fields": {"title": "Another account"}})
        assert forbidden.status_code in (403, 404)
        assert repo.get_current_artifact(f.first.user_id, f.project_id, names.BLUEPRINT).id == active.id
