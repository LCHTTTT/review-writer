# -*- coding: utf-8 -*-
"""Unit tests for fact-level change detection and impact localisation."""
from unittest import mock

from review_writer_core.fact_impact import fact_change_summary, impact_of_fact_change


def matrix(facts_by_paper):
    return {
        "topic": "copper",
        "rows": [
            {"paper_id": paper_id, "scientific_facts": facts}
            for paper_id, facts in facts_by_paper
        ],
    }


F1 = {"fact_id": "f1", "field_id": "yield", "value": "80%"}
F2 = {"fact_id": "f2", "field_id": "yield", "value": "90%"}


def writing_plan():
    return {
        "sections": [
            {
                "section_id": "S01",
                "claims": [
                    {"claim_id": "c1", "fact_ids": ["f1"], "evidence_refs": [{"paper_id": "P1"}]},
                    {"claim_id": "c2", "fact_ids": ["f2"], "evidence_refs": [{"paper_id": "P2"}]},
                    # No fact binding: fallback should still catch it by paper.
                    {"claim_id": "c3", "fact_ids": [], "evidence_refs": [{"paper_id": "P1"}]},
                ],
                "paragraphs": [
                    {"paragraph_id": "S01-p1", "claim_realizations": [{"claim_id": "c1"}]},
                    {"paragraph_id": "S01-p2", "claim_realizations": [{"claim_id": "c2"}]},
                    {"paragraph_id": "S01-p3", "claim_realizations": [{"claim_id": "c3"}]},
                ],
            },
            {
                "section_id": "S02",
                "claims": [
                    {"claim_id": "c1b", "fact_ids": ["f1"], "evidence_refs": [{"paper_id": "P1"}]},
                ],
                "paragraphs": [
                    {"paragraph_id": "S02-p1", "claim_realizations": [{"claim_id": "c1b"}]},
                ],
            },
        ]
    }


def test_unchanged_matrix_has_no_changes():
    old = matrix([("P1", [F1]), ("P2", [F2])])
    summary = fact_change_summary(old, old)
    assert summary["mode"] == "unchanged"
    assert summary["changed_fact_ids"] == []


def test_changed_fact_is_detected_with_paper():
    old = matrix([("P1", [F1]), ("P2", [F2])])
    new = matrix([("P1", [{**F1, "value": "85%"}]), ("P2", [F2])])
    summary = fact_change_summary(old, new)
    assert summary["mode"] == "changed"
    assert summary["changed_fact_ids"] == ["f1"]
    assert summary["changed_paper_ids"] == ["P1"]


def test_deleted_fact_is_detected():
    old = matrix([("P1", [F1]), ("P2", [F2])])
    new = matrix([("P1", []), ("P2", [F2])])
    summary = fact_change_summary(old, new)
    assert summary["mode"] == "changed"
    assert summary["changed_fact_ids"] == ["f1"]


def test_additive_only_uses_existing_classifier():
    old = matrix([("P1", [F1])])
    new = matrix([("P1", [F1, {**F2, "fact_id": "f3"}])])
    with mock.patch("review_writer_core.fact_impact.is_additive_fact_repair", return_value=True):
        summary = fact_change_summary(old, new)
    assert summary["mode"] == "additive_only"


def test_structural_change_is_unknown():
    old = matrix([("P1", [F1])])
    new = matrix([("P1", [F1]), ("P2", [F2])])  # row count changed
    summary = fact_change_summary(old, new)
    assert summary["mode"] == "unknown"


def test_scope_change_is_unknown():
    old = matrix([("P1", [F1])])
    new = {"topic": "different", "rows": [{"paper_id": "P1", "scientific_facts": [F1]}]}
    summary = fact_change_summary(old, new)
    assert summary["mode"] == "unknown"


def test_impact_covers_cross_section_and_paper_fallback():
    plan = writing_plan()
    draft = [
        {"paragraph_id": "S01-p1"},
        {"paragraph_id": "S01-p2"},
        {"paragraph_id": "S02-p1"},
        {"paragraph_key": "S01-p3"},  # manual edit mapped by key
    ]
    impact = impact_of_fact_change(["f1"], ["P1"], plan, draft)
    assert impact["resolution"] == "precise"
    assert impact["affected_paragraph_ids"] == ["S01-p1", "S01-p3", "S02-p1"]
    assert impact["affected_section_ids"] == ["S01", "S02"]
    assert impact["affected_draft_paragraph_ids"] == ["S01-p1", "S01-p3", "S02-p1"]
    # S01-p2 depends on f2, not f1; must stay untouched.
    assert "S01-p2" not in impact["affected_draft_paragraph_ids"]


def test_impact_without_edges_is_full():
    impact = impact_of_fact_change(["f1"], ["P1"], {"sections": []}, [])
    assert impact["resolution"] == "full"
    assert impact["affected_paragraph_ids"] == []
