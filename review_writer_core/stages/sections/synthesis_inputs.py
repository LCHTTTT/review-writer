"""Link source-empty synthesis tasks to existing, bounded chapter evidence."""
from copy import deepcopy
import json
from .evidence_registry import merge_evidence_registry_row


def synthesis_context(dependencies, retained, char_budget=18_000):
    """Whole saved statements as navigation, never an independent evidence source."""
    allowance = max(2, char_budget // max(1, len(dependencies)))
    context = []
    for sid in dependencies:
        writing = retained.get(sid, {}).get("writing") or {}
        candidates = []
        for paragraph in writing.get("paragraphs") or []:
            realizations = [c for c in paragraph.get("claim_realizations") or []
                            if isinstance(c, dict) and str(c.get("text") or "").strip()]
            candidates.extend({"paragraph_id": paragraph.get("paragraph_id"), "text": c["text"]}
                              for c in realizations)
            if not realizations and str(paragraph.get("text") or "").strip():
                candidates.append({"paragraph_id": paragraph.get("paragraph_id"), "text": paragraph["text"]})
        kept, used = [], 0
        for statement in candidates:
            size = len(json.dumps(statement, ensure_ascii=False)) + 2
            if used + size <= allowance:
                kept.append(statement)
                used += size
        context.append({"section_id": sid, "paragraphs": kept, "claims": [],
                        "omitted_context_statements": len(candidates) - len(kept)})
    return context


def link_synthesis_tasks(tasks):
    output = deepcopy(tasks)
    preceding = []
    for task in output:
        explicit = task.get("depends_on_sections") or []
        declared = any(isinstance(item, dict) and item.get("type") == "cross_study_synthesis"
                       for item in task.get("writing_requirements") or [])
        eligible = (task.get("section_role", "body") == "body" and declared
                    and not task.get("primary_papers")
                    and (not task.get("allowed_papers") or task.get("synthesis_contract")))
        if eligible and not task.get("synthesis_contract"):
            candidates = [item for item in preceding if item.get("allowed_papers")
                          and item.get("section_role", "body") == "body"
                          and (item.get("primary_papers") or item.get("supporting_papers"))
                          and (not item.get("synthesis_contract") or item["section_id"] in explicit)]
            if explicit:
                sources = [item for item in candidates if item["section_id"] in explicit]
            else:
                local = []
                for parent in reversed(task.get("parent_headings") or []):
                    local = [item for item in candidates if parent.get("section_id") in
                             {p.get("section_id") for p in item.get("parent_headings") or []}]
                    if local:
                        break
                sources = local if local else candidates
            ids = [item["section_id"] for item in sources]
            papers = list(dict.fromkeys(pid for item in sources
                         for pid in [*(item.get("primary_papers") or []),
                                     *(item.get("supporting_papers") or [])]))
            task["depends_on_sections"] = list(explicit) if explicit else ids
            task["synthesis_contract"] = {"version": 1, "source_sections": ids,
                "selection": "explicit_dependencies" if explicit else "preceding_source_sections",
                "unresolved_dependencies": [sid for sid in explicit if sid not in ids]}
            task["supporting_papers"] = papers
            task["allowed_papers"] = list(dict.fromkeys([*papers, *(task.get("context_papers") or [])]))
            task["writing_mode"] = "cross_section_synthesis"
        preceding.append(task)
    return output


def synthesis_evidence(task, sections):
    contract = task["synthesis_contract"]
    by_id = {section["section_id"]: section for section in sections}
    allowed = set(task.get("allowed_papers") or [])
    registry = {}
    for sid in contract["source_sections"]:
        for hit in by_id.get(sid, {}).get("hits") or []:
            if hit.get("paper_id") not in allowed or not hit.get("evidence_key") or not hit.get("chunk_id") or not str(hit.get("content") or "").strip():
                continue
            # Source eligibility, own-work attribution and identity are never upgraded.
            merge_evidence_registry_row(registry, deepcopy(hit))
    hits = list(registry.values())
    ready = bool(hits) and not contract["unresolved_dependencies"]
    return {"section_id": task["section_id"], "heading": task.get("heading"),
        "query": "", "query_plans": [], "allowed_papers": list(task.get("allowed_papers") or []),
        "retrieval_mode": "lexical" if ready else "insufficient_evidence",
        "status": "ready" if ready else "insufficient_evidence",
        "source_lookup_complete": bool(contract["source_sections"]) and all(sid in by_id for sid in contract["source_sections"]),
        "synthesis_source_sections": list(contract["source_sections"]),
        "synthesis_input_problem": "" if ready else "synthesis_sources_not_ready",
        "hit_count": len(hits), "claim_eligible_hit_count": sum(bool(h.get("claim_eligible", True)) for h in hits),
        "paper_count": len({h["paper_id"] for h in hits}),
        "writeable_primary_papers": [], "context_only_primary_papers": [],
        "unresolved_primary_papers": [], "primary_paper_states": [],
        "abstract_context": [], "hits": hits}


def repair_prepared_synthesis(prepared):
    """Upgrade only orphaned synthesis inputs inside an already validated snapshot."""
    if prepared.get("synthesis_inputs_version") == 1:
        return prepared
    tasks = link_synthesis_tasks(prepared.get("tasks") or [])
    if not any(task.get("synthesis_contract") for task in tasks):
        return prepared
    sections = (prepared.get("evidence_package") or {}).get("sections") or []
    by_id = {s["section_id"]: s for s in sections}
    for task in tasks:
        if not task.get("synthesis_contract"):
            continue
        section = synthesis_evidence(task, sections)
        by_id[task["section_id"]] = section
        task["evidence_status"] = {key: section[key] for key in (
            "retrieval_mode", "status", "hit_count", "claim_eligible_hit_count", "paper_count")}
    prepared["tasks"] = tasks
    prepared["evidence_package"]["sections"] = [by_id[s["section_id"]] for s in sections]
    registry = {}
    for section in prepared["evidence_package"]["sections"]:
        for hit in section.get("hits") or []:
            merge_evidence_registry_row(registry, deepcopy(hit))
    # Existing canonical registry also contains bindings from other evidence channels.
    if any(task.get("synthesis_contract") for task in tasks):
        for hit in prepared["evidence_package"].get("evidence_registry") or []:
            merge_evidence_registry_row(registry, deepcopy(hit))
        prepared["evidence_package"]["evidence_registry"] = list(registry.values())
    snapshot = prepared.get("run_input_snapshot")
    if isinstance(snapshot, dict):
        for task in tasks:
            if task.get("synthesis_contract"):
                snapshot.setdefault("evidence_keys_by_section", {})[task["section_id"]] = sorted(
                    {h["evidence_key"] for h in by_id[task["section_id"]]["hits"] if h.get("evidence_key")})
    prepared["synthesis_inputs_version"] = 1
    return prepared
