"""One project corpus: explicit library choices edit the existing Discovery review.

No model calls, automatic confirmation, second selection table or silent invalidation.
"""
from copy import deepcopy
import math

from review_writer_api.errors import WorkflowConflict, WorkflowNotFound, WorkflowValidationError
from review_writer_api.security import Permission
from review_writer_core.workflow.artifacts import DISCOVERY_REVIEW, MATRIX

MANUAL_GROUP = "project_library_selection"


def recommended_paper_ids(review):
    """Use saved screening decisions, matching the stage-02 picker priority.

    No new relevance scoring: explicit exclusions/invalid scores win across
    duplicate active groups, and web-only candidates are never corpus papers.
    """
    statuses = {}
    for group in review.get("results") or []:
        if group.get("keep") is False:
            continue
        for row in group.get("local_results") or []:
            key = str(row.get("paper_id") or "").strip()
            if not key:
                continue
            try:
                score = float((row.get("score") if row.get("score") is not None else row.get("raw_score", 0)) or 0)
            except (TypeError, ValueError):
                score = 0
            status = 2 if row.get("role") == "excluded" or not math.isfinite(score) or score <= 0 else 1 if row.get("recommendation_status") == "recommended" else 0
            statuses[key] = max(statuses.get(key, -1), status)
    return [key for key, status in statuses.items() if status == 1]


def _snapshot(service, principal, project_id):
    project = service._owned_project(principal, project_id)
    before = service.repository.get_stage_state(principal.user_id, project_id, "discovery")
    review, artifact = service._read_optional_current_json(principal, project_id, DISCOVERY_REVIEW)
    matrix, _ = service._read_optional_current_json(principal, project_id, MATRIX)
    confirmed = list(dict.fromkeys(str(row["paper_id"]) for row in (matrix or {}).get("rows", []) if row.get("paper_id")))
    state = service.repository.get_stage_state(principal.user_id, project_id, "discovery")
    if (before.revision if before else 0) != (state.revision if state else 0):
        raise WorkflowConflict("Paper selection changed while loading. Reload before saving.")
    return project, review, confirmed, state.revision if state else 0, artifact


def library_choices(service, principal, project_id, *, query="", offset=0, limit=50):
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
        raise WorkflowValidationError("Invalid paper list range.")
    _, review, confirmed, revision, artifact = _snapshot(service, principal, project_id)
    selected = service.selected_paper_ids(review) if review is not None else confirmed
    catalog = service._active_library_catalog(principal)
    needle = str(query or "").strip().casefold()[:200]
    rows = sorted(catalog.values(), key=lambda paper: (str(paper.updated_at), paper.paper_id), reverse=True)
    rows = [paper for paper in rows if not needle or needle in str(paper.title).casefold() or needle in paper.paper_id.casefold()]
    return {"scope": "project_selection", "revision": revision, "selected_paper_ids": selected,
            "artifact_id": artifact.id if artifact else "",
            "selected_items": [{"paper_id": key, "title": catalog[key].title if key in catalog else key,
                                "available": key in catalog} for key in selected],
            "confirmed_paper_ids": confirmed, "selection_pending": set(selected) != set(confirmed),
            "total": len(rows), "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(rows) else None,
            "items": [{"paper_id": paper.paper_id, "title": paper.title,
                       "selected": paper.paper_id in selected,
                       "parsed": bool(paper.markdown_relative_path)}
                      for paper in rows[offset:offset + limit]],
            "notice": "Saving selection does not start analysis. Confirm the selection before updating downstream outputs."}


def patch_selection(service, principal, project_id, *, revision, add_ids=(), remove_ids=()):
    principal.require(Permission.PROJECT_WRITE)
    project, current, confirmed, actual, _ = _snapshot(service, principal, project_id)
    if actual != revision:
        raise WorkflowConflict("Paper selection changed. Reload the list before saving; no choices were overwritten.")
    add, remove = set(add_ids), set(remove_ids)
    if add & remove or not add | remove or len(add | remove) > 200 or not all(isinstance(key, str) and key for key in add | remove):
        raise WorkflowValidationError("Specify distinct papers to include or remove (at most 200 per save).")
    catalog = service._active_library_catalog(principal)
    selected = set(service.selected_paper_ids(current)) if current is not None else set(confirmed)
    # Removing a previously selected but now deleted library paper is still possible.
    if add - catalog.keys() or remove - (catalog.keys() | selected):
        raise WorkflowNotFound("One or more papers are unavailable in your library or project.")
    desired = (selected | add) - remove
    if desired == selected and current is not None:
        return library_choices(service, principal, project_id)
    review = deepcopy(current) if current is not None else {"topic": project.topic, "results": []}
    groups = review.setdefault("results", [])
    manual = next((group for group in groups if group.get("system_group") == MANUAL_GROUP), None)
    if manual is None:
        manual = {"keyword": "手动选入文献", "system_group": MANUAL_GROUP, "keep": True, "local_results": [], "web_results": []}
        groups.append(manual)
    # Do not enable ignored search groups; place explicit additions in a kept group.
    manual["keep"] = True
    present = set()
    for group in groups:
        for row in group.get("local_results", []):
            key = str(row.get("paper_id") or "")
            if key in add | remove:
                row["selected_for_matrix"] = key in desired
                if key in add and row.get("role") == "excluded":
                    row["role"] = "uncertain"
            if group.get("keep") is not False and row.get("selected_for_matrix") and row.get("role") != "excluded":
                present.add(key)
    for key in sorted(desired - present):
        if key not in catalog:
            continue
        row = service._local_candidate_from_catalog(catalog[key], reason="Explicitly selected by the user from their library")
        row["selected_for_matrix"] = True
        manual["local_results"].append(row)
    from .discovery import normalize_review
    normalized = normalize_review(review)
    saved = service._publish_review(principal, project_id, normalized, expected_revision=actual)
    choices = library_choices(service, principal, project_id)
    if choices["revision"] != saved["revision"] or choices["artifact_id"] != saved["artifact_id"]:
        raise WorkflowConflict("Paper selection changed again after saving; the saved choices remain, but continuation must reload the latest version.")
    return choices
