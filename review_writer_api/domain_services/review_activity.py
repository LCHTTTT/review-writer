"""Detect active Draft-writing jobs that can invalidate a review snapshot."""
from review_writer_api.workflow_repository import JobRecord

ACTIVE_STATUSES=frozenset({"queued","running","cancel_requested"})
WRITING_JOB_TYPES=frozenset({"draft.rewrite","draft.optimize","draft.accept-rewrite"})


def active_draft_writer(service, user_id, project_id) -> JobRecord | None:
    """Return an active writing job, excluding the review job itself.

    `draft.optimize` also carries peer-review jobs. Their operation key ends
    with `:peer-review`; those do not modify the manuscript, so they must not
    block a new review or the author's decision.
    """
    jobs=service.repository.list_project_jobs(user_id,project_id,limit=50,
        include_payload=False,include_result=False)
    for job in jobs:
        if job.status not in ACTIVE_STATUSES or job.job_type not in WRITING_JOB_TYPES:
            continue
        if job.job_type=='draft.optimize' and str(job.idempotency_scope_key or '').endswith(':peer-review'):
            continue
        return job
    return None
