"""Explicit corpus choices reuse Discovery; isolated, no external model calls."""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from review_writer_api.tests import test_discovery_v1 as fixtures
from review_writer_api.domain_services.project_papers import library_choices, patch_selection
from review_writer_api.errors import WorkflowConflict, WorkflowNotFound
from review_writer_core.workflow.artifacts import MATRIX, DISCOVERY_REVIEW


class ProjectPaperTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.DiscoveryV1Tests("runTest")
        self.case.setUp()
        self.service = self.case.app.state.discovery_service
        self.owner = self.case.first
        self.project_id = self.case.project_id

    def tearDown(self):
        self.case.tearDown()

    def test_fresh_project_selects_library_paper_without_search_then_confirms_same_corpus(self):
        with TestClient(self.case.app) as client:
            endpoint = f"/api/v1/projects/{self.project_id}/discovery/papers"
            empty = client.get(endpoint).json()
            self.assertEqual([], empty["selected_paper_ids"])
            self.assertEqual(3, empty["total"])
            added = client.patch(endpoint, json={"revision": 0, "add_ids": ["P003"]})
            self.assertEqual(200, added.status_code, added.text)
            self.assertEqual(["P003"], added.json()["selected_paper_ids"])
            self.assertEqual([], added.json()["confirmed_paper_ids"])
            self.assertTrue(added.json()["selection_pending"])
            self.assertEqual(["P003"], self.service.get(self.owner, self.project_id)["selected_paper_ids"])
            self.assertIsNone(self.service.repository.get_current_artifact(self.owner.user_id, self.project_id, MATRIX))
            self.assertEqual([], self.service.repository.list_project_jobs(self.owner.user_id, self.project_id))
            confirmed = self.service.confirm(self.owner, self.project_id, added.json()["revision"])
            self.assertEqual(["P003"], [row["paper_id"] for row in confirmed["matrix"]["rows"]])
            current = client.get(endpoint).json()
            self.assertEqual(["P003"], current["confirmed_paper_ids"])
            self.assertFalse(current["selection_pending"])
            removed = client.patch(endpoint, json={"revision": current["revision"], "remove_ids": ["P003"]})
            self.assertEqual(200, removed.status_code, removed.text)
            self.assertEqual([], removed.json()["selected_paper_ids"])
            self.assertEqual(["P003"], removed.json()["confirmed_paper_ids"])
            self.assertEqual(confirmed["matrix_artifact_id"], self.service.repository.get_current_artifact(self.owner.user_id, self.project_id, MATRIX).id)

    def test_versions_foreign_papers_and_missing_projects_are_not_silently_overwritten(self):
        first = patch_selection(self.service, self.owner, self.project_id, revision=0, add_ids=["P001"])
        for kwargs, error in (({"revision": 0, "add_ids": ["P002"]}, WorkflowConflict),
                              ({"revision": first["revision"], "add_ids": ["foreign"]}, WorkflowNotFound)):
            with self.assertRaises(error):
                patch_selection(self.service, self.owner, self.project_id, **kwargs)
        with self.assertRaises(WorkflowNotFound):
            library_choices(self.service, self.case.second, self.project_id)
        with self.assertRaises(WorkflowNotFound):
            patch_selection(self.service, self.case.second, self.case.other_project_id, revision=0, add_ids=["P001"])
        unchanged = patch_selection(self.service, self.owner, self.project_id, revision=first["revision"], add_ids=["P001"])
        self.assertEqual(first["revision"], unchanged["revision"])
        self.assertEqual(["P001"], unchanged["selected_paper_ids"])

    def test_search_paging_and_explicit_addition_do_not_enable_ignored_groups(self):
        with TestClient(self.case.app) as client:
            self.case.discover(client)
            current = self.service.get(self.owner, self.project_id)
            groups = current["results"]
            for group in groups:
                group["keep"] = False
            saved = self.service.save(self.owner, self.project_id, current["revision"], groups)
            selected = patch_selection(self.service, self.owner, self.project_id, revision=saved["revision"], add_ids=["P001"])
            self.assertEqual(["P001"], selected["selected_paper_ids"])
            after = self.service.get(self.owner, self.project_id)
            self.assertTrue(all(group["keep"] is False for group in after["results"][:-1]))
            self.assertTrue(after["results"][-1]["keep"])
        page = library_choices(self.service, self.owner, self.project_id, limit=1)
        self.assertEqual(1, len(page["items"]))
        self.assertEqual(1, page["next_offset"])
        found = library_choices(self.service, self.owner, self.project_id, query="P002")
        self.assertEqual(["P002"], [row["paper_id"] for row in found["items"]])

    def test_conversation_read_and_write_share_the_same_selection(self):
        conversation = self.case.app.state.conversation_service
        action = conversation.actions.proposal(self.owner, self.project_id, "discovery.selection",
                                               {"candidate_ids": ["P002"], "selected": True}, "choose-library")
        conversation.actions.validate_baseline(self.owner, self.project_id, action)
        conversation.actions.execute(self.owner, self.project_id, action, "choose-library")
        read = conversation.execute_read(self.owner, self.project_id, "project_papers", {})
        self.assertEqual(["P002"], read["selected_paper_ids"])
        self.assertEqual(["P002"], self.service.get(self.owner, self.project_id)["selected_paper_ids"])
        self.assertEqual([], read["confirmed_paper_ids"])

    def test_corrupt_existing_review_is_not_replaced_by_an_empty_selection(self):
        patch_selection(self.service, self.owner, self.project_id, revision=0, add_ids=["P001"])
        current = self.service.repository.get_current_artifact(self.owner.user_id, self.project_id, DISCOVERY_REVIEW)
        resolved = self.service.artifacts.resolve_owned_artifact(self.owner.user_id, current.id)
        resolved.path.write_text("broken JSON", encoding="utf-8")
        with self.assertRaises(WorkflowConflict):
            patch_selection(self.service, self.owner, self.project_id, revision=1, add_ids=["P002"])
        self.assertEqual(current.id, self.service.repository.get_current_artifact(self.owner.user_id, self.project_id, DISCOVERY_REVIEW).id)
