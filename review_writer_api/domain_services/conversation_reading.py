"""Read-only chapter projections; never regenerate, verify or publish prose."""
from __future__ import annotations

import json

from review_writer_api.errors import WorkflowNotFound, WorkflowValidationError


def section_view(raw, *, section_id="", paragraph_id="", view="prose"):
    if view not in {"prose", "evidence"} or not isinstance(section_id, str) or not isinstance(paragraph_id, str):
        raise WorkflowValidationError("Invalid chapter reading scope.")
    try:
        index = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise WorkflowValidationError("Saved chapter data could not be read.") from exc
    if not isinstance(index, dict) or not isinstance(index.get("sections"), list):
        raise WorkflowValidationError("Saved chapter data has no section list.")
    sections = [item for item in index["sections"] if isinstance(item, dict)]
    if not section_id:
        if paragraph_id or view == "evidence":
            raise WorkflowValidationError("Select a section ID before reading paragraph evidence.")
        # The directory itself is paged, so even a very large outline remains bounded.
        directory = [{"section_id": s.get("section_id"), "heading": s.get("heading") or s.get("title") or "",
                      "paragraph_count": len(s.get("paragraphs") or [])} for s in sections]
        return json.dumps(directory, ensure_ascii=False), {"view": "directory", "scope": "sections", "section_count": len(sections)}
    selected = sections if section_id == "*" else [s for s in sections if s.get("section_id") == section_id]
    if not selected:
        raise WorkflowNotFound("Saved chapter not found. Use the directory's exact section ID.")
    if view == "evidence" and (section_id == "*" or not paragraph_id):
        raise WorkflowValidationError("Select one paragraph to inspect its evidence.")
    output = []
    found = False
    for section in selected:
        paragraphs = [p for p in section.get("paragraphs") or [] if isinstance(p, dict)]
        if paragraph_id:
            paragraphs = [p for p in paragraphs if p.get("paragraph_id") == paragraph_id]
            if not paragraphs:
                continue
        found = True
        if view == "evidence":
            # Keep only the selected paragraph's saved scientific records, not unrelated audits.
            claims = [c for c in section.get("claims") or [] if isinstance(c, dict) and
                      (c.get("paragraph_id") == paragraph_id or c.get("claim_id") in (paragraphs[0].get("claim_ids") or []))]
            fields = ("paragraph_id", "text", "paper_id", "cited_paper_ids", "claim_ids", "claims",
                      "claim_realizations", "evidence", "evidence_refs", "source_verification")
            output.append(json.dumps({"paragraph": {k: paragraphs[0][k] for k in fields if k in paragraphs[0]},
                                      "section_claims": claims}, ensure_ascii=False))
        else:
            output.append(f"## {section.get('heading') or section.get('title') or section.get('section_id')} [{section.get('section_id')}]")
            if paragraphs:
                for p in paragraphs:
                    output.append(f"[{p.get('paragraph_id') or ''}]\n{p.get('text') or ''}")
                    if p.get("cited_paper_ids"):
                        output.append("Saved citation IDs: " + json.dumps(p["cited_paper_ids"], ensure_ascii=False))
            elif not paragraph_id:
                output.append(str(section.get("draft_md") or "[No saved prose paragraphs]"))
    if not found:
        raise WorkflowNotFound("Saved paragraph not found in that chapter.")
    return "\n\n".join(output), {"view": view, "scope": "sections", "section_id": section_id,
                                 "paragraph_id": paragraph_id, "offset_unit": "characters_in_selected_view"}
