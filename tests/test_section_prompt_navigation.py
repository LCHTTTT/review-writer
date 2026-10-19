import json
from copy import deepcopy

from review_writer_core.stages.sections.prompt_navigation import contribution_guides, chapter_navigation
from review_writer_core.stages.sections.prompt_navigation import legacy_navigation


def test_historical_resume_preserves_old_navigation_without_mislabeling_it():
    assert legacy_navigation({"proposed": {}})
    assert legacy_navigation({"request_snapshot": {"prompt": "paid request"}})
    assert not legacy_navigation(None)
    assert not legacy_navigation({"request_snapshot": {"navigation_version": 2}})


def test_writer_script_keeps_legacy_guides_but_new_guides_use_shown_papers():
    import runpy
    from review_writer_core.review_fact_readiness import REVIEW_READING_STRATEGY
    script = runpy.run_path("skills/review-section-drafting-figure-picking/scripts/generate_section_drafts.py")
    task = {"allowed_papers": ["P1", "P2"], "reading_strategy": REVIEW_READING_STRATEGY}
    rows = {pid: {"paper_analysis": {"contribution": "A bounded contribution", "review_topic": "Topic",
                                   "fact_ids": ["F1"], "evidence_keys": ["K1"]}} for pid in ["P1", "P2"]}
    legacy = script["section_contribution_guides"](task, rows, "Topic", paper_ids=["P1"], legacy=True)
    assert set(legacy) == {"P1", "P2"} and legacy["P1"]["evidence_keys"] == ["K1"]
    new = script["section_contribution_guides"](task, rows, "Topic", paper_ids=["P1"])
    assert set(new) == {"P1"} and "evidence_keys" not in new["P1"]


def test_contribution_guides_do_not_repeat_identifiers_or_unshown_papers():
    raw = {f"P{i}": {"contribution": "A bounded contribution.", "research_question": "A specific question?",
        "topic_relation": "Relevant to scope.", "fact_ids": ["fact" * 100] * 100,
        "evidence_keys": ["key" * 100] * 100, "usage": "navigation_only"} for i in range(113)}
    before = deepcopy(raw)
    compact = contribution_guides(raw, ["P0", "P1"])
    assert set(compact) == {"P0", "P1"}
    assert compact["P0"]["contribution"] == raw["P0"]["contribution"]
    assert "fact_ids" not in json.dumps(compact)
    assert "evidence_keys" not in json.dumps(compact)
    assert raw == before


def test_global_guidance_budget_omits_whole_cues_not_scientific_substrings():
    raw = {f"P{i}": {"contribution": "x" * 400, "research_question": "y" * 300,
                      "topic_relation": "z" * 200} for i in range(113)}
    compact = contribution_guides(raw, raw)
    assert len(json.dumps(compact, ensure_ascii=False)) <= 12000
    for pid, item in compact.items():
        for key, text in item.items():
            if key != "usage":
                assert text == raw[pid][key]


def test_chapter_navigation_retains_related_ownership_not_paragraph_plans():
    rows = [{"section_id": f"S{i}", "heading": f"Heading {i}", "review_problem": "question " * 90,
        "paragraph_tasks": ["large task " * 100], "writing_objective": "objective " * 100,
        "primary_papers": [f"P{i}"], "supporting_papers": [], "avoid_points": ["Do not claim causal superiority."]}
        for i in range(28)]
    before = deepcopy(rows)
    nav = chapter_navigation(rows, section_id="S27", paper_ids=["P2"])
    assert len(json.dumps(nav, ensure_ascii=False)) <= 6000
    assert nav[0]["section_id"] == "S2" and nav[0]["primary_papers"] == ["P2"]
    assert all(item.get("section_id") != "S27" for item in nav)
    assert "paragraph_tasks" not in json.dumps(nav)
    assert "writing_objective" not in json.dumps(nav)
    assert rows == before
