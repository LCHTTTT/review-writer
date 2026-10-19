"""Stable queue names used by PostgreSQL workers and fairness limits."""

from __future__ import annotations


JOB_QUEUES = frozenset({"scientific", "image", "ingest", "document", "bibliography", "model"})
DELEGATED_TEXT_PARENT_JOB_TYPES = frozenset({"sections.generate", "matrix.enrich", "draft.optimize"})


def delegated_text_parent(job_type, payload):
    if job_type=='draft.optimize':
        return payload.get('revision_mode')=='peer_review' and payload.get('flow_policy')=='review-revision/2'
    return job_type in DELEGATED_TEXT_PARENT_JOB_TYPES

INTERACTIVE_JOB_TYPES = frozenset({
    "draft.rewrite", "draft.optimize", "draft.synthesis", "final.conclusion", "conversation.turn", "conversation.prepare", "conversation.control",
})
IMAGE_JOB_TYPES = frozenset({"figures.redraw", "final.overview"})
DOCUMENT_JOB_TYPES = frozenset({"final.export", "final.pdf"})
INGEST_JOB_TYPES = frozenset(
    {
        "library.upload",
        "library.archive",
        "library.index",
        "library.semantic-backfill",
        "library.search",
        "library.download",
        # Discovery owns acquiring its own candidates; the download machinery is
        # reused, but the chain belongs to one stage.
        "discovery.acquire",
    }
)


def queue_for_job_type(job_type: str) -> str:
    """Map a public job type to a small, deployment-stable worker queue."""

    normalized = str(job_type or "").strip()
    if normalized in {"model.dispatch", "conversation.turn", "conversation.prepare"}:
        return "model"
    if normalized in {"conversation.control", "conversation.session", "conversation.attachment"}:
        return "document"  # deterministic acknowledgement; the stop fence is already durable
    if normalized == "library.bibliography-audit":
        return "bibliography"
    if normalized in IMAGE_JOB_TYPES:
        return "image"
    if normalized in DOCUMENT_JOB_TYPES:
        return "document"
    if normalized in INGEST_JOB_TYPES:
        return "ingest"
    return "scientific"
