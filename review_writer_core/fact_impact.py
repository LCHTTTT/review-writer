"""Fact-level change detection and downstream impact localisation.

P1 of surgical invalidation: instead of marking the whole Draft stale when the
Matrix changes, locate the facts that actually changed and reverse-lookup the
paragraphs that depend on them. The dependency edges (claim.fact_ids,
paragraph.claim_realizations.claim_id) already exist; this module connects them.

Unknown / structural changes stay conservative (``mode == "unknown"``) and the
caller keeps the existing whole-stage behaviour.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from review_writer_core.scientific_facts import is_additive_fact_repair


def fact_change_summary(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare two Matrix snapshots at fact granularity.

    ``mode``:
      - ``unchanged``     : no facts changed.
      - ``additive_only`` : only new source-bound facts were added (compatible).
      - ``changed``       : existing facts were modified/deleted; ids are listed.
      - ``unknown``       : structure/scope changed; cannot localise safely.
    """
    if not previous or not current:
        return {"mode": "unknown", "changed_fact_ids": [], "changed_paper_ids": []}
    if is_additive_fact_repair(previous, current):
        return {"mode": "additive_only", "changed_fact_ids": [], "changed_paper_ids": []}

    before, after = dict(previous), dict(current)
    old_rows, new_rows = before.pop("rows", []), after.pop("rows", [])
    before.pop("fact_repair_history", None)
    after.pop("fact_repair_history", [])
    if before != after or not old_rows or len(old_rows) != len(new_rows):
        # Rows added/removed or scope/classification changed: whole-stage.
        return {"mode": "unknown", "changed_fact_ids": [], "changed_paper_ids": []}

    changed_fact_ids: list[str] = []
    changed_paper_ids: list[str] = []
    for old_row, new_row in zip(old_rows, new_rows):
        old, new = dict(old_row), dict(new_row)
        old_facts, new_facts = old.pop("scientific_facts", []), new.pop("scientific_facts", [])
        if old != new:
            # A paper's non-fact fields changed; treat conservatively.
            return {"mode": "unknown", "changed_fact_ids": [], "changed_paper_ids": []}
        removed = [fact for fact in old_facts if fact not in new_facts]
        if not removed:
            continue
        for fact in removed:
            fact_id = _text((fact or {}).get("fact_id"))
            if fact_id and fact_id not in changed_fact_ids:
                changed_fact_ids.append(fact_id)
        paper_id = _text(new.get("paper_id") or old.get("paper_id"))
        if paper_id and paper_id not in changed_paper_ids:
            changed_paper_ids.append(paper_id)

    if changed_fact_ids:
        return {"mode": "changed", "changed_fact_ids": changed_fact_ids, "changed_paper_ids": changed_paper_ids}
    return {"mode": "unchanged", "changed_fact_ids": [], "changed_paper_ids": []}


def _text(value: Any) -> str:
    return str(value or "").strip()


def impact_of_fact_change(
    changed_fact_ids: Iterable[str],
    changed_paper_ids: Iterable[str],
    writing_plan: Mapping[str, Any],
    draft_paragraphs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reverse-lookup which chapter/draft paragraphs depend on changed facts.

    ``resolution`` is ``precise`` when the claim/paragraph edges exist, else
    ``full`` (caller keeps whole-stage invalidation).
    """
    fact_claim: dict[str, set[str]] = {}
    claim_para: dict[str, set[tuple[str, str]]] = {}
    claim_papers: dict[str, set[str]] = {}

    for section in (writing_plan.get("sections") or []):
        if not isinstance(section, dict):
            continue
        section_id = _text(section.get("section_id"))
        for claim in section.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            claim_id = _text(claim.get("claim_id"))
            if not claim_id:
                continue
            for fact_id in claim.get("fact_ids") or []:
                fact_claim.setdefault(_text(fact_id), set()).add(claim_id)
            papers = {
                _text(ref.get("paper_id"))
                for ref in (claim.get("evidence_refs") or [])
                if isinstance(ref, dict) and _text(ref.get("paper_id"))
            }
            claim_papers[claim_id] = papers
        for paragraph in section.get("paragraphs") or []:
            if not isinstance(paragraph, dict):
                continue
            paragraph_id = _text(paragraph.get("paragraph_id"))
            for realization in paragraph.get("claim_realizations") or []:
                if not isinstance(realization, dict):
                    continue
                claim_para.setdefault(_text(realization.get("claim_id")), set()).add((paragraph_id, section_id))

    affected_paragraph_ids: set[str] = set()
    affected_section_ids: set[str] = set()

    for fact_id in changed_fact_ids:
        for claim_id in fact_claim.get(fact_id, ()):
            for paragraph_id, section_id in claim_para.get(claim_id, ()):
                affected_paragraph_ids.add(paragraph_id)
                affected_section_ids.add(section_id)

    # Fallback: a claim with no fact binding but citing the changed paper.
    changed_paper_set = {_text(value) for value in changed_paper_ids}
    for claim_id, papers in claim_papers.items():
        if papers & changed_paper_set:
            for paragraph_id, section_id in claim_para.get(claim_id, ()):
                affected_paragraph_ids.add(paragraph_id)
                affected_section_ids.add(section_id)

    draft_ids: set[str] = set()
    for paragraph in draft_paragraphs:
        if not isinstance(paragraph, dict):
            continue
        identifier = _text(paragraph.get("paragraph_id") or paragraph.get("paragraph_key"))
        if identifier and identifier in affected_paragraph_ids:
            draft_ids.add(identifier)

    resolution = "precise" if (fact_claim or claim_para) else "full"
    return {
        "affected_paragraph_ids": sorted(affected_paragraph_ids),
        "affected_section_ids": sorted(affected_section_ids),
        "affected_draft_paragraph_ids": sorted(draft_ids),
        "resolution": resolution,
    }
