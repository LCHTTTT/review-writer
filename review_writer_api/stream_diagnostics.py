"""Bounded, content-free wire observations; never interpret them as an answer."""

import re


def observe_event(diagnostics, event):
    fields = diagnostics.setdefault("wire_fields", {})
    remaining = 256

    def visit(value, path, depth):
        nonlocal remaining
        if remaining <= 0 or depth > 7:
            return
        remaining -= 1
        kind = ("null" if value is None else "boolean" if isinstance(value, bool)
                else "object" if isinstance(value, dict) else "array" if isinstance(value, list)
                else "string" if isinstance(value, str) else "number" if isinstance(value, (int, float))
                else "other")
        identity = path + ":" + kind
        if identity not in fields and len(fields) >= 128:
            diagnostics["wire_fields_capped"] = True
            return
        row = fields.setdefault(identity, {"events": 0})
        row["events"] += 1
        if isinstance(value, str):
            row["chars"] = row.get("chars", 0) + len(value)
            row["nonempty"] = row.get("nonempty", 0) + bool(value)
        if isinstance(value, dict):
            for key, item in list(value.items())[:64]:
                safe = key if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", key) else "<key>"
                visit(item, path + "." + safe, depth + 1)
        elif isinstance(value, list):
            for item in value[:8]:
                visit(item, path + "[]", depth + 1)

    visit(event, "$", 0)
    for choice in (event.get("choices") or [])[:8]:
        if isinstance(choice, dict):
            bucket = "first_choice_events" if choice.get("index", 0) == 0 else "other_choice_events"
            diagnostics[bucket] = diagnostics.get(bucket, 0) + 1


def request_options(body):
    """Keep only non-sensitive output controls, not messages, input, or headers."""
    result = {"stream": bool(body.get("stream"))}
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if isinstance(body.get(key), (int, float)) and not isinstance(body[key], bool):
            result[key] = body[key]
    effort = body.get("reasoning_effort")
    if effort in ("none", "minimal", "low", "medium", "high", "xhigh"):
        result["reasoning_effort"] = effort
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") in ("enabled", "disabled", "adaptive"):
        result["thinking_type"] = thinking["type"]
    return result
