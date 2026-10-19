"""Bounded navigation projections; authoritative sources are never modified."""
import json
from copy import deepcopy

NAVIGATION_VERSION = 2
CONTRIBUTION_CHAR_BUDGET = 12_000
CHAPTER_CHAR_BUDGET = 6_000


def legacy_navigation(resume_state):
    return bool(resume_state) and (resume_state.get("request_snapshot") or {}).get("navigation_version", 1) < NAVIGATION_VERSION


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def contribution_guides(guides, paper_ids, *, char_budget=CONTRIBUTION_CHAR_BUDGET):
    """Keep narrative cues, not a second inventory of fact/passage identifiers."""
    ids = list(dict.fromkeys(str(pid) for pid in paper_ids))
    result = {}
    allowance = max(0, (char_budget - 2) // max(1, len(ids)))
    for pid in ids:
        original = guides.get(pid) or {}
        if not original:
            continue
        item = {"usage": "navigation_only"}
        for key in ("contribution", "research_question", "topic_relation"):
            value = original.get(key)
            proposed = {**item, key: value}
            if value and size({pid: proposed}) <= allowance:
                item[key] = deepcopy(value)
        if len(item) > 1 and size({**result, pid: item}) <= char_budget:
            result[pid] = item
    return result


def chapter_navigation(rows, *, section_id=None, paper_ids=(), char_budget=CHAPTER_CHAR_BUDGET):
    """Preserve whole relevant scope cues without importing every chapter's plan."""
    papers = set(str(pid) for pid in paper_ids)
    candidates = []
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("section_id")) == str(section_id):
            continue
        primary = [pid for pid in row.get("primary_papers") or [] if str(pid) in papers]
        supporting = [pid for pid in row.get("supporting_papers") or [] if str(pid) in papers]
        item = {key: deepcopy(row[key]) for key in ("section_id", "heading") if row.get(key)}
        if primary:
            item["primary_papers"] = primary
        if supporting:
            item["supporting_papers"] = supporting
        candidates.append((not bool(primary or supporting), row, item))
    result = []
    # Evidence-sharing chapters take priority; never truncate questions or caveats.
    for _, row, item in sorted(candidates, key=lambda value: value[0]):
        if size([*result, item]) <= char_budget:
            result.append(item)
    for item in result:
        row = next(row for _, row, base in candidates if base is item)
        for key in ("review_problem", "avoid_points"):
            if row.get(key):
                proposed = {**item, key: deepcopy(row[key])}
                trial = [proposed if old is item else old for old in result]
                if size(trial) <= char_budget:
                    item[key] = proposed[key]
    return result
