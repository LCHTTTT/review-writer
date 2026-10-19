from copy import deepcopy

from review_writer_core.stages.sections.synthesis_inputs import (
    link_synthesis_tasks, synthesis_evidence, repair_prepared_synthesis,
    synthesis_context,
)
from review_writer_core.stages.sections.coverage import section_input_fingerprints
from review_writer_core.stages.sections.execution import run_sections


def source(sid, pid, parent=None):
    return {"section_id": sid, "section_role": "body", "primary_papers": [pid],
        "supporting_papers": [], "context_papers": [], "allowed_papers": [pid],
        "parent_headings": [{"section_id": parent}] if parent else []}


def synthesis(sid="arbitrary-comparison", **extra):
    return {"section_id": sid, "section_role": "body", "primary_papers": [],
        "supporting_papers": [], "context_papers": [], "allowed_papers": [],
        "writing_requirements": [{"type": "cross_study_synthesis"}], **extra}


def hit(pid="paper-A", key="key-A", **extra):
    return {"paper_id": pid, "evidence_key": key, "chunk_id": "chunk-A", "content": "Original source",
        "claim_eligible": True, "support_level": "direct", "fact_ids": ["F1"], **extra}


def test_links_declared_synthesis_without_changing_primary_ownership():
    original = [source("one", "paper-A"), source("two", "paper-B"), synthesis()]
    linked = link_synthesis_tasks(original)
    assert linked[:2] == original[:2]
    assert linked[-1]["primary_papers"] == []
    assert linked[-1]["supporting_papers"] == ["paper-A", "paper-B"]
    assert linked[-1]["depends_on_sections"] == ["one", "two"]
    assert link_synthesis_tasks(linked) == linked
    assert original[-1]["allowed_papers"] == []


def test_ordinary_empty_section_and_introduction_are_not_synthesis():
    ordinary = {**synthesis(), "writing_requirements": []}
    intro = {**synthesis("intro"), "section_role": "introduction"}
    tasks = [source("one", "paper-A"), ordinary, intro]
    assert link_synthesis_tasks(tasks) == tasks


def test_explicit_dependencies_and_parent_scope_prevent_broadening():
    tasks = [source("unrelated", "paper-A", "other"), source("local", "paper-B", "group")]
    bounded = link_synthesis_tasks([*tasks, synthesis(parent_headings=[{"section_id": "group"}])])[-1]
    assert bounded["depends_on_sections"] == ["local"]
    explicit = link_synthesis_tasks([*tasks, synthesis(depends_on_sections=["unrelated"])])[-1]
    assert explicit["allowed_papers"] == ["paper-A"]


def test_missing_explicit_dependency_does_not_silently_use_whole_library():
    task = link_synthesis_tasks([source("one", "paper-A"), synthesis(depends_on_sections=["missing"])])[-1]
    package = synthesis_evidence(task, [{"section_id": "one", "hits": [hit()]}])
    assert package["retrieval_mode"] == "insufficient_evidence"
    assert package["hits"] == []
    assert task["synthesis_contract"]["unresolved_dependencies"] == ["missing"]


def test_reuses_canonical_passages_and_all_fact_bindings_without_promoting_context():
    task = link_synthesis_tasks([source("one", "paper-A"), source("two", "paper-A"), synthesis()])[-1]
    context = hit(key="context", claim_eligible=False, support_level="abstract_limited")
    sections = [{"section_id": "one", "hits": [hit(fact_bindings=[{"fact_id": "F1"}]), context]},
        {"section_id": "two", "hits": [hit(fact_ids=["F2"], fact_bindings=[{"fact_id": "F2"}])]}]
    before = deepcopy(sections)
    result = synthesis_evidence(task, sections)
    assert result["retrieval_mode"] == "lexical"
    assert len(result["hits"]) == 2
    assert result["hits"][0]["fact_ids"] == ["F1", "F2"]
    assert len(result["hits"][0]["fact_bindings"]) == 2
    assert result["hits"][1]["claim_eligible"] is False
    assert sections == before


def test_legacy_snapshot_upgrade_keeps_independent_chapter_fingerprints():
    tasks = [source("one", "paper-A"), synthesis()]
    evidence = [{"section_id": "one", "hits": [hit()]}, {"section_id": tasks[-1]["section_id"], "hits": []}]
    blueprint = {"sections": [{"section_id": t["section_id"]} for t in tasks]}
    snapshot = {"tasks": tasks, "evidence_package": {"sections": evidence}, "run_input_snapshot": {}}
    before = section_input_fingerprints(tasks, {s["section_id"]: s for s in evidence}, [], blueprint, {})
    repaired = repair_prepared_synthesis(deepcopy(snapshot))
    after = section_input_fingerprints(repaired["tasks"], {s["section_id"]: s for s in repaired["evidence_package"]["sections"]}, [], blueprint, {})
    assert before["one"] == after["one"]
    assert before[tasks[-1]["section_id"]] != after[tasks[-1]["section_id"]]
    assert repair_prepared_synthesis(deepcopy(repaired)) == repaired


def test_retry_runs_only_synthesis_and_receives_completed_chapter_context():
    tasks = link_synthesis_tasks([source("one", "paper-A"), synthesis()])
    completed = {"one": {"writing": {"paragraphs": [{"text": "Previously audited body"}], "claims": []}}}
    called = []
    def generate(task, phase):
        called.append(task)
        return {"writing": {"paragraphs": []}}
    run_sections(tasks, generate, lambda *a: None, lambda *a: None, completed=completed)
    assert len(called) == 1
    assert called[0]["dependency_context"][0]["paragraphs"][0]["text"] == "Previously audited body"


def test_context_budget_keeps_whole_saved_statements_and_each_source_visible():
    saved = {sid: {"writing": {"paragraphs": [{"paragraph_id": "para", "text": "large body" * 1000,
        "claim_realizations": [{"text": "A bounded finding."}, {"text": "A second finding."}]}]}}
        for sid in ["one", "two"]}
    context = synthesis_context(["one", "two"], saved, char_budget=200)
    assert [c["section_id"] for c in context] == ["one", "two"]
    assert all(c["paragraphs"] for c in context)
    assert all(p["text"] in {"A bounded finding.", "A second finding."}
               for c in context for p in c["paragraphs"])
    assert all(not c["claims"] for c in context)
