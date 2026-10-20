#!/usr/bin/env python3
"""Dry-run report for retiring the legacy review_pdf_checks table.

This script never drops a table or deletes rows. It reports whether any
queued/running peer-review job still uses the retired PDF-check payload and
whether the table still contains author attestations. A deployment should only
add a drop migration after this report is clean and the data is no longer needed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from review_writer_api.config import database_url_from_env
from review_writer_api.database import create_session_factory
from review_writer_api.workflow_models import ReviewPdfCheck, WorkflowJob

ACTIVE = frozenset({"queued", "running", "cancel_requested"})


def main() -> int:
    database_url = database_url_from_env()
    if not database_url:
        print("No database URL configured; nothing was inspected.")
        return 2
    factory, _engine = create_session_factory(database_url)
    with factory() as session:
        checks = session.scalar(select(func.count()).select_from(ReviewPdfCheck)) or 0
        jobs = session.scalars(select(WorkflowJob).where(
            WorkflowJob.job_type.in_(("draft.optimize", "draft.rewrite")))).all()
    legacy = [job for job in jobs
              if (job.payload_json or {}).get("revision_mode") in
              ("peer_review", "peer_review_recommendation")
              and (job.payload_json or {}).get("pdf_check_ids")]
    active = [job for job in legacy if job.status in ACTIVE]
    historical = [job for job in legacy if job.status not in ACTIVE]
    print(f"review_pdf_checks rows: {checks}")
    print(f"legacy PDF-check jobs total: {len(legacy)} (active: {len(active)}, terminal: {len(historical)})")
    for job in active:
        print(f"  ACTIVE {job.id} {job.job_type} {job.status}")
    if active:
        print("BLOCKED: finish or cancel the active legacy jobs before any cleanup.")
        return 1
    if checks:
        print("BLOCKED: the immutable author attestations still exist. "
              "Keep the table or archive/export it before adding a drop migration.")
        return 1
    print("SAFE TO RETIRE: no active legacy jobs and no PDF-check rows remain. "
          "Add a separate drop migration only after a database backup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
