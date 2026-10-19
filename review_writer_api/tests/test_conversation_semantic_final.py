"""Existing editable Draft -> Final through semantic tools and original services."""
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from review_writer_api.tests import figure_test_support
from review_writer_api.tests import test_drafts_v1 as draft_fixtures


class SemanticFinalTests(unittest.TestCase):
    def setUp(self):
        self.case = draft_fixtures.DraftsV1Tests("runTest")
        self.case.offline_text_provider_key = "offline-not-a-secret"
        self.calls = []
        original = figure_test_support.create_app
        def model(prompt, index):
            base = json.loads(prompt)["context"]
            self.calls.append(base)
            return {"reply": "", "tools": [{"name": "execute_intent", "arguments": {
                "goal": "final", "operation": "generate_review", "auto_adopt": True,
                "request_quote": base["user"]}}]}
        def app(*args, **kwargs):
            kwargs["native_workflow_overrides"] = {**kwargs.get("native_workflow_overrides", {}), "conversation.model": model}
            return original(*args, **kwargs)
        with patch.object(figure_test_support, "create_app", side_effect=app):
            self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_polite_semantic_command_keeps_edited_draft_and_builds_final_once(self):
        with TestClient(self.case.app) as client:
            self.case.prepare_draft(client)
            service = self.case.app.state.drafts_service
            before = service.get(self.case.first, self.case.project_id)
            paragraph = before["paragraphs"][0]
            edit = client.put(f"/api/v1/projects/{self.case.project_id}/draft/paragraphs/{paragraph['paragraph_id']}",
                json={"revision": before["revision"], "text": "Manually clarified supported paragraph. [1]",
                      "base_text_sha256": paragraph["text_sha256"]})
            self.assertEqual(200, edit.status_code, edit.text)
            before_id = service.get(self.case.first, self.case.project_id)["draft_artifact_id"]
            message = "能帮我把现在这份稿件做完吗？按推荐的继续就好"
            endpoint = f"/api/v1/projects/{self.case.project_id}/conversation/messages"
            submitted = client.post(endpoint, json={"message": message}, headers=self.case.headers("semantic-final"))
            self.assertEqual(202, submitted.status_code, submitted.text)
            turn = self.case.wait_job(client, submitted.json()["id"])
            self.assertEqual("succeeded", turn["status"], turn)
            plan = self.case.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(["draft.approve", "final.build"], [row["action"] for row in plan["result"]["completed"]])
            self.assertEqual(before_id, service.get(self.case.first, self.case.project_id)["draft_artifact_id"])
            final = self.case.app.state.final_service.get(self.case.first, self.case.project_id)
            self.assertTrue(final["final_current"])
            self.assertEqual(1, len(self.calls))
            self.assertEqual(submitted.json()["id"], client.post(endpoint, json={"message": message}, headers=self.case.headers("semantic-final")).json()["id"])
            self.assertEqual(1, len(self.calls))
