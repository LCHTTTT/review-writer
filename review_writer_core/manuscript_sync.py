"""Deterministic three-way prose merge. Never resolve overlapping manual edits by AI."""
from difflib import SequenceMatcher
from dataclasses import dataclass

@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    lines: tuple[str, ...]


def _edits(base, value):
    return [Edit(a, b, tuple(value[c:d])) for tag, a, b, c, d in SequenceMatcher(None, base, value, autojunk=False).get_opcodes() if tag != "equal"]


def _overlaps(a, b):
    if a.start == a.end and b.start == b.end:
        return a.start == b.start
    if a.start == a.end:
        return b.start <= a.start < b.end
    if b.start == b.end:
        return a.start <= b.start < a.end
    return max(a.start, b.start) < min(a.end, b.end)


def merge_prose(base: str, upstream: str, draft: str) -> dict:
    """Apply only non-overlapping upstream edits; return current manual text on conflict.

    Line granularity is deliberately conservative: two edits on the same prose
    line are conflicts even when a human might merge their words. Unknown/absent
    baselines must be handled by the caller, not treated as an empty baseline.
    """
    if any(not isinstance(text, str) for text in (base, upstream, draft)):
        raise ValueError("Three real text versions are required.")
    original = base.splitlines(keepends=True)
    incoming = _edits(original, upstream.splitlines(keepends=True))
    manual = _edits(original, draft.splitlines(keepends=True))
    applied, conflicts = [], []
    for edit in incoming:
        if edit in manual:
            continue  # both sides made the exact same edit
        overlaps = [local for local in manual if _overlaps(edit, local)]
        if overlaps:
            conflicts.append({"base_start_line": edit.start + 1, "base_end_line": edit.end,
                "base_text": "".join(original[edit.start:edit.end]), "upstream_text": "".join(edit.lines),
                "draft_edits": [{"base_start_line": item.start + 1, "base_end_line": item.end, "text": "".join(item.lines)} for item in overlaps]})
        else:
            applied.append(edit)
    combined = list(manual) + applied
    value = list(original)
    for edit in sorted(combined, key=lambda item: (item.start, item.end), reverse=True):
        value[edit.start:edit.end] = edit.lines
    return {"text": "".join(value), "conflicts": conflicts, "applied_count": len(applied),
            "manual_edit_count": len(manual), "complete": not conflicts, "granularity": "line_conservative"}
