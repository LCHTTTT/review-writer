"""Explicit opt-in for providers supporting DeepSeek's thinking switch.

No name-based capability guesses. Defaults and full-manuscript/upstream audits
remain untouched. The allowlist must be confirmed by the administrator.
"""
VERIFICATION_STAGES = frozenset({"Evidence-bound peer review", "Automatic candidate source check",
                               "Paragraph source reference correction"})


def verification_options(model, wire, stage, confirmed_models=()):
    if (wire in {"chat", "chat-completion", "chat-completions"}
            and model in confirmed_models
            and (stage in VERIFICATION_STAGES or stage.startswith("matrix-facts-"))):
        return {"thinking": {"type": "disabled"}}
    return {}
