"""Isolated shared three-way sync preserving manual draft and manuscript fields."""
import json
from fastapi.testclient import TestClient
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_core.workflow import artifacts as names

class DraftSyncTests(NativeFigureApiTestCase):
    from review_writer_api.tests.test_drafts_v1 import DraftsV1Tests as _Helpers
    prepare_draft = _Helpers.prepare_draft

    def upstream(self, replacement, heading=None):
        repo = self.app.state.workflow_repository
        record = repo.get_current_artifact(self.first.user_id, self.project_id, names.SECTION_DRAFTS)
        path = self.app.state.artifact_service.resolve_owned_artifact(self.first.user_id, record.id).path
        data = json.loads(path.read_text())
        data["sections"][0]["paragraphs"][0]["text"] = replacement
        if heading is not None:
            data["sections"][0]["heading"] = heading
        run = repo.create_stage_run(self.first.user_id, self.project_id, "sections", status="succeeded")
        artifact = self._publish(run.id, names.SECTION_DRAFTS, "new-index.json", json.dumps(data).encode(), "json")
        repo.set_current_artifact(self.first.user_id, self.project_id, names.SECTION_DRAFTS, artifact.id)
        return artifact

    def test_disjoint_sync_preserves_manual_paragraph_and_rejects_stale_preview(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            base = f"/api/v1/projects/{self.project_id}/draft"
            before = client.get(base).json()
            second = next(row for row in before["paragraphs"] if row["paragraph_id"] == "S1-p2")
            manual = client.put(base + "/paragraphs/S1-p2", json={"revision": before["revision"], "text": second["text"] + " Manual clarification.", "base_text_sha256": second["text_sha256"]})
            self.assertEqual(200, manual.status_code, manual.text)
            self.upstream("Updated grounded paragraph.")
            preview = client.get(base + "/upstream-sync/S1")
            self.assertEqual(200, preview.status_code, preview.text)
            value = preview.json()
            self.assertEqual([], value["conflicts"])
            self.assertIn("Manual clarification.", value["candidate_text"])
            result = client.post(base + "/upstream-sync/S1", json={"expected_draft_artifact_id": value["draft_artifact_id"], "expected_sections_artifact_id": value["sections_artifact_id"]})
            self.assertEqual(200, result.status_code, result.text)
            text = client.get(base).json()["first_draft_md"]
            self.assertIn("Updated grounded paragraph", text)
            self.assertIn("Manual clarification.", text)
            stale = client.post(base + "/upstream-sync/S1", json={"expected_draft_artifact_id": value["draft_artifact_id"], "expected_sections_artifact_id": value["sections_artifact_id"]})
            self.assertEqual(409, stale.status_code)

    def test_heading_changes_are_not_silently_omitted(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            base = f"/api/v1/projects/{self.project_id}/draft"
            before = client.get(base).json()["first_draft_md"]
            self.upstream("Changed prose.", heading="New heading")
            result = client.get(base + "/upstream-sync/S1")
            self.assertEqual(409, result.status_code, result.text)
            self.assertEqual(before, client.get(base).json()["first_draft_md"])

    def test_repeated_sync_uses_saved_baseline_and_unchanged_save_is_read_only(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            base = f"/api/v1/projects/{self.project_id}/draft"
            for incoming in ("First update.", "Second update."):
                self.upstream(incoming)
                value = client.get(base + "/upstream-sync/S1").json()
                self.assertEqual([], value["conflicts"])
                result = client.post(base + "/upstream-sync/S1", json={"expected_draft_artifact_id": value["draft_artifact_id"], "expected_sections_artifact_id": value["sections_artifact_id"]})
                self.assertEqual(200, result.status_code, result.text)
                self.assertIn(incoming.removesuffix("."), client.get(base).json()["first_draft_md"])
            value = client.get(base + "/upstream-sync/S1").json()
            self.assertEqual([], value["changes"])
            result = client.post(base + "/upstream-sync/S1", json={"expected_draft_artifact_id": value["draft_artifact_id"], "expected_sections_artifact_id": value["sections_artifact_id"]})
            self.assertEqual(200, result.status_code, result.text)
            self.assertTrue(result.json()["unchanged"])
            self.assertEqual(value["draft_artifact_id"], result.json()["draft_artifact_id"])
            self.assertEqual(value["revision"], result.json()["revision"])

    def test_overlapping_edit_returns_conflict_and_does_not_write(self):
        with TestClient(self.app) as client:
            self.prepare_draft(client)
            base = f"/api/v1/projects/{self.project_id}/draft"
            before = client.get(base).json()
            first = next(row for row in before["paragraphs"] if row["paragraph_id"] == "S1-p1")
            result = client.put(base + "/paragraphs/S1-p1", json={"revision": before["revision"], "text": "Manual replacement.", "base_text_sha256": first["text_sha256"]})
            self.assertEqual(200, result.status_code, result.text)
            self.upstream("Different upstream replacement.")
            preview = client.get(base + "/upstream-sync/S1")
            self.assertEqual(200, preview.status_code, preview.text)
            value = preview.json()
            self.assertTrue(value["conflicts"])
            result = client.post(base + "/upstream-sync/S1", json={"expected_draft_artifact_id": value["draft_artifact_id"], "expected_sections_artifact_id": value["sections_artifact_id"]})
            self.assertEqual(409, result.status_code, result.text)
            self.assertIn("Manual replacement.", client.get(base).json()["first_draft_md"])
