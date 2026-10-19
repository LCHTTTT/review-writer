"""Offline audit packing, evidence preservation and paid-request recovery."""
from copy import deepcopy
import json

import pytest

from review_writer_core.stages.sections import source_audit as audit
from review_writer_core.stages.sections.source_writing import CHECK_SCHEMA
from review_writer_core.model_gateway_client import DeferredModelCall


def payload(count=18, text_size=1800):
    claims, sources = [], []
    for i in range(count):
        text = f"Study {i}: yield 82%, ee 94%, 25 C; control without catalyst failed. " + "Evidence context. " * text_size
        ref = {"evidence_key": f"E{i}", "paper_id": f"P{i}", "quote": text,
               "page_start": i + 1, "source_lineage_hash": "v1"}
        claims.append({"claim_id": f"C{i}", "paragraph_id": f"p{i // 2}", "claim": f"Finding {i}.",
            "evidence_refs": [ref, deepcopy(ref)], "result_context": [], "claim_kind": "reported_finding",
            "assertion_ceiling": "direct_source_report"})
        sources.append({"evidence_key": f"E{i}", "paper_id": f"P{i}", "content": text,
                        "verified_facts": [{"summary": text}]})
    return {"claims": claims, "sources": sources, "paragraphs": [{"paragraph_id": f"p{i}"} for i in range((count+1)//2)],
            "allow_style_repair": True, "style_findings": {}, "prose_context": [],
            "review_reasons": {c["claim_id"]: [] for c in claims}, "review_scope": "whole_section",
            "chapter_responsibilities": [{"section_id": "other", "heading": "Other", "redundant_archive": "X" * 26000}]}


def model(prompt, schema, label):
    data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"claims":'):])[0]
    if label == "section-prose-coherence-check":
        assert "sources" not in data and "source_spans" not in data
        assert data["claims"] == []
    return {"claims": [{"claim_id": c["claim_id"], "status": "supported", "text": "", "reason": "", "conflict_quote": ""}
                       for c in data["claims"]],
            "section_review": {"status": "coherent", "issues": [], "paragraph_order": []}}


def run(data, call=model, state=None, **kwargs):
    state = state if state is not None else {}
    return audit.run_audit(instructions="Source checks.\n", payload=data,
        navigation=[{"section_id": "other", "heading": "Other"}], schema=CHECK_SCHEMA,
        call=call, state=state, persist=lambda: None, **kwargs)


def test_compaction_keeps_exact_sources_conditions_counterexamples_and_provenance():
    data = payload(3, 5)
    before = deepcopy(data)
    compact = audit.compact_payload(data, [{"section_id": "other", "heading": "Other"}])
    assert data == before
    assert len(compact["source_spans"]) == 3
    refs = {s["span_id"]: s for s in compact["source_spans"]}
    for original, changed in zip(data["claims"], compact["claims"]):
        for old, new in zip(original["evidence_refs"], changed["evidence_refs"]):
            assert {k: v for k, v in refs[new["span_id"]].items() if k != "span_id"} == old
    assert [s["content"] for s in compact["sources"]] == [s["content"] for s in data["sources"]]
    assert "redundant_archive" not in json.dumps(compact["chapter_responsibilities"])


def test_rich_chapter_navigation_does_not_force_one_claim_per_request():
    data = payload(count=14, text_size=3)
    navigation = [{"section_id": f"S{i}", "heading": f"Chapter {i}",
        "review_problem": "Review question " * 60, "primary_papers": [f"P{i}"],
        "planning_snapshot": {"original_objective": "objective " * 100},
        "paragraph_tasks": ["paragraph task " * 100]} for i in range(28)]
    compact = audit.compact_payload(data, navigation)
    prompts = audit.audit_requests(audit.BATCH_CHECK_GUIDANCE, compact)
    assert len(prompts) == 2
    assert all(len(prompt) <= audit.MAX_PROMPT_CHARS for prompt in prompts)
    batches = [json.JSONDecoder().raw_decode(p[p.index('{"claims":'):])[0] for p in prompts]
    assert [c["claim_id"] for b in batches for c in b["claims"]] == [c["claim_id"] for c in data["claims"]]
    assert all(source["content"] in "".join(prompts) for source in data["sources"])


def test_batch_projection_keeps_escaped_chemistry_text_and_exact_ref_values():
    data = payload(count=2, text_size=3)
    text = 'ZnBr2 (10 mol%), "DMSO"\n82% yield; 94% ee; a negative control failed.'
    data["sources"][0]["content"] = text
    for ref in data["claims"][0]["evidence_refs"]:
        ref["quote"] = text
    compact = audit.compact_payload(data, [])
    prompt = audit.audit_requests(audit.BATCH_CHECK_GUIDANCE, compact)[0]
    decoded = json.JSONDecoder().raw_decode(prompt[prompt.index('{"claims":'):])[0]
    source = next(row for row in decoded["sources"] if row["evidence_key"] == "E0")
    assert source["content"] == text
    quote = next(row for row in decoded["source_spans"] if row["evidence_key"] == "E0")
    assert {k:v for k,v in quote.items() if k != "span_id"} == data["claims"][0]["evidence_refs"][0]


def test_legacy_paid_plan_keeps_pending_prompt_and_legacy_coherence_navigation():
    import hashlib
    data = payload(count=2, text_size=3)
    navigation = [{"section_id": "other", "heading": "Other", "review_problem": "Q" * 37000}]
    compact = audit.compact_payload(data, navigation)
    compact["chapter_responsibilities"] = navigation
    prompts = []
    for claim in compact["claims"]:
        batch = audit.select_claims(compact, [claim])
        batch["chapter_responsibilities"] = navigation
        prompts.append(audit.BATCH_CHECK_GUIDANCE + audit.SPAN_GUIDANCE + audit.encode(batch))
    keys = [hashlib.sha256(("section-used-claim-check" + p + audit.encode(CHECK_SCHEMA)).encode()).hexdigest() for p in prompts]
    state = {"source_audit_plan": {"input_key": hashlib.sha256(audit.encode(
        {"payload": data, "navigation": navigation, "prose": None}).encode()).hexdigest(), "request_keys": keys},
        "source_audit_requests": {key: {"prompt": prompt, "schema": CHECK_SCHEMA, "label": "section-used-claim-check"}
                                  for key, prompt in zip(keys, prompts)}}
    state["source_audit_requests"][keys[0]]["result"] = model(prompts[0], CHECK_SCHEMA, "section-used-claim-check")
    calls = []
    def recover(prompt, schema, label):
        calls.append((prompt, label))
        return model(prompt, schema, label)
    audit.run_audit(instructions="Source checks.\n", payload=data, navigation=navigation,
        schema=CHECK_SCHEMA, call=recover, state=state, persist=lambda: None)
    assert calls[0] == (prompts[1], "section-used-claim-check")
    assert prompts[0] not in [p for p, _ in calls]
    coherence = json.JSONDecoder().raw_decode(calls[-1][0][calls[-1][0].index('{"claims":'):])[0]
    assert coherence["chapter_responsibilities"] == navigation


def test_batches_are_bounded_and_coherence_has_full_prose_but_no_evidence():
    data = payload(text_size=40)
    calls, state = [], {}
    def checked(prompt, schema, label):
        calls.append((prompt, label))
        if label == "section-prose-coherence-check":
            body = json.loads(prompt[prompt.index('{"claims":'):])
            assert len(body["prose_context"]) == len(data["claims"])
        return model(prompt, schema, label)
    result = run(data, checked, state)
    assert [c["claim_id"] for c in result["claims"]] == [c["claim_id"] for c in data["claims"]]
    batches = [p for p, label in calls if label == "section-used-claim-check"]
    assert len(batches) > 1 and all(len(p) <= audit.MAX_PROMPT_CHARS for p in batches)
    assert calls[-1][1] == "section-prose-coherence-check"
    assert state["source_audit_metrics"]["oversized_atomic_batches"] == 0


def test_paper_label_repair_stays_in_existing_batch_and_keeps_source_identity():
    data = payload(count=9, text_size=5)
    data["claims"][0]["claim"] = "P502 reported an 82% yield."
    pid = "P5021405023598897354"
    for ref in data["claims"][0]["evidence_refs"]:
        ref["paper_id"] = pid
    data["sources"][0]["paper_id"] = pid
    data["style_findings"] = {"C0": ["internal_paper_identifier"]}
    original = deepcopy(data)
    calls = []
    def checked(prompt, schema, label):
        calls.append(label)
        body = json.JSONDecoder().raw_decode(prompt[prompt.index('{"claims":'):])[0]
        result = model(prompt, schema, label)
        if any(c["claim_id"] == "C0" for c in body["claims"]):
            assert body["style_findings"]["C0"] == ["internal_paper_identifier"]
            assert body["source_spans"][0]["paper_id"] == pid
            result["claims"][0].update(status="rewritten", text="The reaction afforded an 82% yield.")
        return result
    result = run(data, checked)
    assert data == original
    assert result["claims"][0]["text"] == "The reaction afforded an 82% yield."
    assert calls == ["section-used-claim-check", "section-used-claim-check", "section-prose-coherence-check"]


@pytest.mark.parametrize("failure", ["deferred", "provider", "malformed"])
def test_resume_reuses_completed_batches_and_keeps_pending_request_exact(failure):
    data, state, calls = payload(text_size=40), {}, []
    def interrupted(prompt, schema, label):
        calls.append((prompt, label))
        if len(calls) == 2:
            if failure == "deferred":
                raise DeferredModelCall("waiting-batch")
            if failure == "provider":
                raise RuntimeError("provider unavailable")
            return {"claims": [], "section_review": {"status": "coherent", "issues": []}}
        return model(prompt, schema, label)
    with pytest.raises(DeferredModelCall if failure == "deferred" else RuntimeError):
        run(data, interrupted, state)
    resumed = []
    def recovered(prompt, schema, label):
        resumed.append((prompt, label))
        return model(prompt, schema, label)
    result = run(data, recovered, state)
    assert calls[0] not in resumed
    assert calls[1] == resumed[0]
    assert len(result["claims"]) == len(data["claims"])
    assert run(data, lambda *args: pytest.fail("Completed calls cannot replay"), state) == result


def test_legacy_pending_preserves_exact_original_request_and_small_audit_stays_single():
    large = payload()
    captured = []
    run(large, lambda p, s, l: captured.append((p, l)) or {}, legacy_pending=True)
    assert captured == [("Source checks.\n" + audit.encode(large), "section-used-claim-check")]
    small = payload(1, 1)
    small["chapter_responsibilities"] = []
    captured.clear()
    run(small, lambda p, s, l: captured.append((p, l)) or {})
    assert len(captured) == 1 and "source_spans" not in captured[0][0]


def test_oversized_indivisible_evidence_is_reported_and_never_silently_clipped():
    data, state = payload(1, 2000), {}
    run(data, state=state)
    assert state["source_audit_metrics"]["oversized_atomic_batches"] == 1
    record = next(r for r in state["source_audit_requests"].values() if r["label"] == "section-used-claim-check")
    assert data["sources"][0]["content"] in record["prompt"]


@pytest.mark.parametrize("failure", ["provider", "deferred"])
def test_real_writer_resume_keeps_paid_batch_prompt_and_prose(failure):
    from review_writer_core.stages.sections.source_writing import write_from_sources
    evidence = {"evidence_key": "base", "paper_id": "P1", "chunk_id": "base", "claim_eligible": True,
                "assertion_ceiling": "direct_source_report", "source_lineage_hash": "v1",
                "content": "The approach was feasible in the pilot cohort."}
    text = "The approach was feasible in the pilot cohort."
    saved, calls = {}, []
    def save(value):
        saved.clear()
        saved.update(deepcopy(value))
    def writer_model(prompt, schema, label):
        calls.append((prompt, label))
        if label == "section-source-writing":
            return {"paragraphs": [{"role": "anchor_case", "reader_takeaway": text, "text": text,
                "claims": [{"text": text, "claim_kind": "reported_finding", "result_context": [],
                            "support_spans": [{"evidence_key": "E001", "quote": text}]}]} for _ in range(9)]}
        audits = [c for c in calls if c[1] == "section-used-claim-check"]
        if label == "section-used-claim-check" and len(audits) == 2:
            if failure == "deferred":
                raise DeferredModelCall("batch-two")
            raise RuntimeError("Provider unavailable")
        return model(prompt, schema, label)
    inputs = {"section_id": "S1", "task": {"allowed_papers": ["P1"], "primary_papers": ["P1"]},
              "context": "", "evidence": [evidence], "audit_mode": "full", "call": writer_model, "save_state": save}
    with pytest.raises(DeferredModelCall if failure == "deferred" else RuntimeError):
        write_from_sources(**inputs)
    before = deepcopy(saved["proposed"])
    old = [c for c in calls if c[1] == "section-used-claim-check"]
    resumed = []
    def recover(prompt, schema, label):
        resumed.append((prompt, label))
        assert label != "section-source-writing"
        return model(prompt, schema, label)
    write_from_sources(**{**inputs, "call": recover}, resume_state=deepcopy(saved))
    assert old[0] not in resumed
    assert old[1] == resumed[0]
    assert saved["proposed"] == before


def test_resource_setting_changes_do_not_repack_an_inflight_paid_plan(monkeypatch):
    data, state, calls = payload(text_size=40), {}, []
    def interrupted(prompt, schema, label):
        calls.append((prompt, label))
        if len(calls) == 2:
            raise DeferredModelCall("pending")
        return model(prompt, schema, label)
    with pytest.raises(DeferredModelCall):
        run(data, interrupted, state)
    monkeypatch.setattr(audit, "MAX_CLAIMS", 1)
    monkeypatch.setitem(CHECK_SCHEMA, "description", "Schema wording changed after admission")
    resumed = []
    def recover(prompt, schema, label):
        resumed.append((prompt, label))
        if label == "section-used-claim-check":
            assert "description" not in schema  # retained admitted schema, not a new paid call
        return model(prompt, schema, label)
    run(data, recover, state)
    assert resumed[0] == calls[1]
    assert calls[0] not in resumed
