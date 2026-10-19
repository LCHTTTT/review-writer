"""Bounded structural reads and rolling tool context, without model calls."""
import json


def stage_summary(value, *, budget=16000):
    remaining, truncated = budget, False
    def visit(item, depth=0):
        nonlocal remaining, truncated
        if remaining <= 0 or depth > 5:
            truncated = True
            return {"omitted": True}
        if isinstance(item, str):
            limit = min(1800, max(0, remaining))
            remaining -= min(len(item), limit)
            if len(item) > limit:
                truncated = True
                return {"excerpt": item[:limit], "total_chars": len(item), "complete": False}
            return item
        if isinstance(item, list):
            selected = []
            for child in item[:12]:
                if remaining <= 0:
                    break
                remaining -= 20
                selected.append(visit(child, depth + 1))
            if len(selected) != len(item):
                truncated = True
                return {"items": selected, "total_items": len(item), "complete": False}
            return selected
        if isinstance(item, dict):
            selected = {}
            for key, child in item.items():
                if remaining <= 0:
                    truncated = True
                    break
                remaining -= len(key) + 20
                selected[key] = visit(child, depth + 1)
            return selected
        remaining -= 20
        return item
    result = visit(value)
    return {"details": result, "complete": not truncated,
            "note": "Stage overview only; use read_artifact/read_section/read_paper for full content."}


def tool_context(steps, *, budget=65000):
    """Omit older read bodies explicitly; preserve persisted results for retries."""
    used, selected = 0, []
    for batch in reversed([s.get("results", []) for s in steps]):
        bounded = []
        for result in reversed(batch):
            size = len(json.dumps(result, ensure_ascii=False))
            if used + size <= budget:
                bounded.append(result)
                used += size
            else:
                value = result.get("value") or {}
                refs = {k: v for k, v in value.items() if k in {
                    "artifact_id", "paper_id", "section_id", "paragraph_id", "next_offset", "total_chars", "view"
                }} if isinstance(value, dict) else {}
                bounded.append({"tool": result.get("tool"), "status": result.get("status"),
                    "value": {**refs, "context_omitted": True, "complete": False,
                              "note": "Older read omitted from this round; reread its source if needed."}})
        selected.append(list(reversed(bounded)))
    return list(reversed(selected))
