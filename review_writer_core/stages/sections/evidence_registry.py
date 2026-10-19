"""Canonical passage identities shared by retrieval and synthesis projections."""

_SUPPORT_LEVEL_RANK = {"coverage_only": 0, "context_only": 1, "abstract_limited": 2, "direct": 3}


def strongest_support_level(*levels):
    values = [str(value or "coverage_only") for value in levels]
    return max(values, key=lambda item: _SUPPORT_LEVEL_RANK.get(item, -1))


def merge_evidence_registry_row(registry, hit):
    key = str(hit.get("evidence_key") or "")
    if not key:
        return
    existing = registry.get(key)
    if existing is None:
        registry[key] = dict(hit)
        return
    for field in ("fact_ids", "question_ids", "retrieval_passes", "asset_refs",
                  "mechanism_evidence_types", "normalized_fact_values"):
        existing[field] = list(dict.fromkeys(str(value) for value in
            [*(existing.get(field) or []), *(hit.get(field) or [])] if str(value)))
    existing["support_level"] = strongest_support_level(existing.get("support_level"), hit.get("support_level"))
    existing["claim_eligible"] = bool(existing.get("claim_eligible") or hit.get("claim_eligible"))
    existing["counts_as_evidence"] = bool(existing.get("counts_as_evidence") or hit.get("counts_as_evidence"))
    if existing.get("normalized_fact_values"):
        existing["normalized_fact_value"] = "\n".join(existing["normalized_fact_values"])
    existing["fact_bindings"] = list({binding["fact_id"]: binding for binding in
        [*(existing.get("fact_bindings") or []), *(hit.get("fact_bindings") or [])]}.values())
