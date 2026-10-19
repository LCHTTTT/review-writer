import pytest
from review_writer_core.stages.planning.topic_recommendation import validate_recommendation, normalize_recommended_outline


def test_recommendation_keeps_domain_specific_questions_without_a_fixed_taxonomy():
    result = validate_recommendation({"organization": "Measurement scales", "sections": [
        {"title": "Spatial resolution", "role": "body", "question": "Which scales can be resolved?",
         "paper_ids": ["P1"], "rationale": "The selected study reports scale-dependent measurements."}
    ]}, [{"paper_id": "P1"}])
    assert "Spatial resolution" in result["outline_md"]
    assert "Which scales" in result["outline_md"]
    assert result["topic_outline_intent"]["system_recommended"]


def test_recommended_title_and_classification_label_use_the_same_normalized_title():
    result = validate_recommendation({"organization": "Synthesis routes", "sections": [
        {"title": "Copper-catalyzed   alkyne routes", "role": "body",
         "question": "How do ligands affect selectivity and substrate scope?",
         "paper_ids": ["P1"], "rationale": "Compare supported results and boundaries."},
    ]}, [{"paper_id": "P1"}])
    assert "## Copper-catalyzed alkyne routes" in result["outline_md"]
    assert "Purpose: How do ligands affect selectivity and substrate scope?" in result["outline_md"]
    assert result["topic_outline_intent"]["classification_contract"]["axes"][0]["partitions"][0]["label"] == "Copper-catalyzed alkyne routes"


def test_duplicate_recommended_titles_keep_a_distinct_supported_question():
    result = validate_recommendation({"organization": "Methods", "sections": [
        {"title": "Catalysis", "role": "body", "question": "What is established for copper?", "paper_ids": ["P1"], "rationale": "Copper evidence."},
        {"title": "Catalysis", "role": "body", "question": "What is established for palladium?", "paper_ids": ["P2"], "rationale": "Palladium evidence."},
    ]}, [{"paper_id": "P1"}, {"paper_id": "P2"}])
    assert "## What is established for palladium?" in result["outline_md"]
    labels = [partition["label"] for partition in result["topic_outline_intent"]["classification_contract"]["axes"][0]["partitions"]]
    assert labels == ["Catalysis", "What is established for palladium?"]


def test_recommendation_rejects_unknown_papers_instead_of_inventing_a_template():
    with pytest.raises(ValueError):
        validate_recommendation({"sections": [{"title": "Results", "role": "body", "question": "Why?", "paper_ids": ["unknown"]}]}, [{"paper_id": "P1"}])
    with pytest.raises(ValueError):
        validate_recommendation({}, [{"paper_id": "P1"}])


def test_recommendation_structure_keeps_body_comparison_but_leaves_conclusion_to_final():
    source = "# Outline\n## Evidence limitations\nSection role: body\nPurpose: compare methods.\n## Background\nSection role: introduction\nPurpose: define scope.\n## Outlook\nSection role: conclusion\nPurpose: conclude."
    result = normalize_recommended_outline(source)
    assert result.index("## Introduction") < result.index("## Evidence limitations")
    assert "define scope" in result
    assert "## Outlook" not in result
    assert normalize_recommended_outline(result) == result


def test_missing_introduction_is_a_plan_not_invented_scientific_history():
    result = normalize_recommended_outline("# Outline\n## Methods\nSection role: body\nPurpose: compare the studies.")
    assert "## Introduction\nSection role: introduction" in result
    assert "supported source context" in result
    assert result.index("## Introduction") < result.index("## Methods")


def test_hierarchy_routes_actual_papers_to_leaves_not_parent_categories():
    from review_writer_core.stages.planning.outline import outline_sections
    from review_writer_api.domain_services.sections import SectionsService
    result = validate_recommendation({"organization": "Strategies", "sections": [
        {"title": "Main strategy", "role": "body", "question": "What approaches are established?", "paper_ids": ["P1", "P2"],
         "children": [
             {"title": "Approach A", "role": "body", "question": "How does A solve the problem?", "paper_ids": ["P1"]},
             {"title": "Approach B", "role": "body", "question": "How does B differ?", "paper_ids": ["P2"], "supporting_paper_ids": ["P1"]},
         ]}
    ]}, [{"paper_id": "P1"}, {"paper_id": "P2"}])
    parsed = outline_sections(result["outline_md"])
    parent = next(s for s in parsed if s["title"] == "Main strategy")
    assert parent["organizing_only"] and not parent["paper_ids"]
    leaves = [s for s in parsed if s["section_role"] == "body" and not s.get("organizing_only")]
    assert [s["heading_level"] for s in leaves] == [3, 3]
    assert [s["paper_ids"] for s in leaves] == [["P1"], ["P2"]]
    assert leaves[1]["supporting_paper_ids"] == ["P1"]
    assert all(s["parent_section_id"] == parent["section_id"] for s in leaves)
    assert normalize_recommended_outline(result["outline_md"]) == result["outline_md"]
    blueprint = {"sections": [{**s, "primary_papers": s["paper_ids"], "major_papers": s["paper_ids"]} for s in parsed],
                 "paper_placement_policy": {"mode": "argument_based"}}
    tasks = SectionsService.tasks_from_blueprint(blueprint)
    assert set(p for t in tasks for p in t["primary_papers"]) == {"P1", "P2"}
    assert all(t["section_id"] != parent["section_id"] for t in tasks)


def test_empty_subsection_is_a_gap_not_a_draft_and_missing_paper_is_not_forced():
    result = validate_recommendation({"sections": [{"title": "Methods", "role": "body", "question": "Compare?", "children": [
        {"title": "Established", "role": "body", "question": "Which results?", "paper_ids": ["P1"]},
        {"title": "Unsupported scope", "role": "body", "question": "What is missing?", "paper_ids": []}
    ]}]}, [{"paper_id": "P1"}, {"paper_id": "P2"}])
    assert "### Established" in result["outline_md"]
    assert "Unsupported scope" not in result["outline_md"]
    routing = result["topic_outline_intent"]["routing_diagnostics"]
    assert routing["unassigned_paper_count"] == 1
    assert routing["unused_papers"][0]["paper_id"] == "P2"
    assert routing["unsupported_sections"][0]["title"] == "Unsupported scope"


def test_nested_unknown_and_duplicate_primary_routes_are_not_silently_accepted():
    for ids in (["unknown"], ["P1"]):
        with pytest.raises(ValueError):
            validate_recommendation({"sections": [{"title": "Methods", "role": "body", "question": "Compare?", "children": [
                {"title": "A", "role": "body", "question": "A?", "paper_ids": ["P1"]},
                {"title": "B", "role": "body", "question": "B?", "paper_ids": ids}
            ]}]}, [{"paper_id": "P1"}])


def test_comparison_support_routes_do_not_steal_a_later_primary_owner():
    from review_writer_core.stages.planning.outline import outline_sections, outline_markdown_from_sections
    from review_writer_core.review_structure import assign_primary_paper_sections
    result = validate_recommendation({"sections": [
        {"title": "Comparison", "role": "body", "question": "How do they compare?", "supporting_paper_ids": ["P1", "P2"]},
        {"title": "Method A", "role": "body", "question": "A?", "paper_ids": ["P1"]},
        {"title": "Method B", "role": "body", "question": "B?", "paper_ids": ["P2"]},
    ]}, [{"paper_id": "P1"}, {"paper_id": "P2"}])
    parsed = outline_sections(result["outline_md"])
    normalized, owners = assign_primary_paper_sections(parsed, ["P1", "P2"])
    comparison = next(s for s in normalized if s["title"] == "Comparison")
    assert comparison["primary_papers"] == []
    assert comparison["supporting_papers"] == ["P1", "P2"]
    assert all(s["title"] != "Comparison" for s in normalized if s["section_id"] in owners.values())
    roundtrip = outline_sections(outline_markdown_from_sections(parsed, outline_style="topic-guided"))
    assert next(s for s in roundtrip if s["title"] == "Comparison")["supporting_paper_ids"] == ["P1", "P2"]


def test_support_only_source_has_a_real_route_and_is_not_reported_as_missing():
    result = validate_recommendation({"sections": [
        {"title": "Cross-study evidence", "role": "body", "question": "What can be compared?", "supporting_paper_ids": ["P1"]}
    ]}, [{"paper_id": "P1"}])
    routing = result["topic_outline_intent"]["routing_diagnostics"]
    assert routing["unassigned_paper_count"] == 0
    assert routing["supporting_paper_count"] == 1
    assert "Supporting papers: P1." in result["outline_md"]


def test_recommendation_input_reuse_requires_same_corpus_scope_not_missing_fingerprints():
    from review_writer_core.stages.planning.topic_recommendation import recommendation_inputs_match
    assert not recommendation_inputs_match({}, {})
    assert recommendation_inputs_match({"input_fingerprint": "old", "source_scope_fingerprint": "same"},
                                       {"input_fingerprint": "new-facts", "source_scope_fingerprint": "same"})
    assert not recommendation_inputs_match({"input_fingerprint": "old", "source_scope_fingerprint": "same"},
                                           {"input_fingerprint": "new", "source_scope_fingerprint": "new-corpus"})


def test_existing_facts_enrich_navigation_without_mandatory_facts_or_extra_reads():
    import json
    from review_writer_core.stages.planning.topic_recommendation import recommendation_paper_context
    row = {"paper_id": "P1", "title": "Actual work", "paper_analysis": {"review_topic": "New topic",
        "research_question": "Improve measurement", "contribution": "Higher resolution", "topic_relation": "Relevant"},
        "scientific_facts": [
            {"field_id": "result", "value": "Verified measurement", "evidence_refs": [{"paper_id": "P1"}], "support_level": "direct"},
            {"field_id": "result", "value": "Rejected result", "evidence_refs": [{}], "verification": {"status": "rejected"}},
        ]}
    result = recommendation_paper_context(row, abstract="Original abstract", topic="New topic", budget=1500)
    assert result["contribution_navigation"]["contribution"] == "Higher resolution"
    assert result["fact_navigation"][0]["value"] == "Verified measurement"
    assert "Rejected result" not in json.dumps(result)
    assert len(json.dumps(result)) < 1800
    assert recommendation_paper_context({"paper_id": "P2"}, abstract="", topic="New topic", budget=300)["fact_navigation"] == []
