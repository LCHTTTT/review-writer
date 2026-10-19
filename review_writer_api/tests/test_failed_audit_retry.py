from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from review_writer_api.job_handlers.stage_execution import prepare_failed_audit_retry


@pytest.mark.parametrize("status,retry,parent,expected", [
    ("failed", "old", "old", True), ("running", "old", "old", False),
    ("failed", None, "old", False), ("failed", "old", "other", False),
])
def test_only_explicit_retry_of_failed_legacy_audit_switches_to_batching(status, retry, parent, expected):
    checkpoint = {"entries": {"S01": {"text": "Completed chapter"}}, "authoring_states": {
        "S04": {"state": {"audit_pending": True, "repair_attempted": True, "proposed": {"text": "Drafted prose"}}}}}
    before = deepcopy(checkpoint)
    source = SimpleNamespace(id="old", status="failed", result={"waiting_model_job_ids": ["child"]})
    child = SimpleNamespace(project_id="project", status=status, job_type="model.dispatch", payload={
        "parent_job_id": parent, "stage": "section-used-claim-check", "prompt": "Check\n" + json.dumps({
            "claims": [{"claim_id": "S04-p1-C01", "paragraph_id": "S04-p1"}], "allow_style_repair": True})})
    context = SimpleNamespace(user_id="user", project_id="project", retry_of_job_id=retry,
                              repository=SimpleNamespace(get_job=lambda *_: child))
    prepare_failed_audit_retry(context, source, checkpoint)
    state = checkpoint["authoring_states"]["S04"]["state"]
    assert ("audit_pending" not in state) is expected
    assert checkpoint["entries"] == before["entries"]
    assert state["proposed"] == before["authoring_states"]["S04"]["state"]["proposed"]
    if expected:
        assert state["source_audit_allow_style_repair"] is True
