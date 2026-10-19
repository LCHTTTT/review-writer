"""Condensed reader reaction table: projection, de-duplication, column choice."""

from review_writer_core.section_reaction_table import (
    CORE_COLUMNS,
    group_reaction_rows,
    project_reaction_rows,
    render_reaction_table,
    select_table_style,
    validate_reaction_table,
)


def _section(records, *, heading="Aldehydes", role="body"):
    refs, contexts = [], []
    for record in records:
        refs.append({"evidence_key": record["evidence_key"], "paper_id": record["paper"],
                     "quote": "source passage"})
        contexts.append({k: v for k, v in record.items() if k != "paper"})
    return {
        "section_id": "S01",
        "heading": heading,
        "section_role": role,
        "paragraphs": [{
            "paragraph_id": "S01-p1",
            "claim_realizations": [{
                "claim_id": "c1",
                "citation_group": [record["paper"] for record in records],
                "evidence_refs": refs,
                "result_context": contexts,
            }],
        }],
    }


def _new(paper, key, object_, catalyst, env, result):
    return {"evidence_key": key, "paper": paper, "object": object_, "catalyst": catalyst,
            "conditions": "", "environment": env, "result": result, "units": ""}


def test_split_cells_project_into_condensed_columns():
    section = _section([
        _new("P1", "E1", "propargylic alcohols + aldehydes", "CuBr, pyrrolidine", "DCM, rt", "92% yield"),
        _new("P2", "E2", "terminal alkynes + ketones", "chiral phosphoric acid", "toluene, 60 C", "78% yield"),
    ])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    assert [row["transformation"] for row in rows] == [
        "propargylic alcohols + aldehydes", "terminal alkynes + ketones"]
    assert rows[0]["catalyst"] == "CuBr, pyrrolidine"
    assert rows[0]["conditions"] == "DCM, rt"
    rendered = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1)
    assert "Reaction system / transformation" in rendered
    assert "Key catalyst system" in rendered
    assert "Conditions / environment" in rendered
    assert rendered.count("| --- |") >= 1
    assert "<!-- reaction_table:" in rendered
    assert "Selectivity" not in rendered  # no ee/de in this chapter


def test_caption_sits_immediately_before_the_table():
    section = _section([
        _new("P1", "E1", "aldehydes", "CuBr", "DCM", "80% yield"),
        _new("P2", "E2", "ketones", "Pd", "THF", "70% yield"),
    ])
    md = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1)
    lines = [line for line in md.splitlines() if line.strip()]
    # The caption must bind to the table (manuscript parser + LaTeX numbering),
    # so nothing may sit between them.
    assert lines[0].startswith("Table 1.")
    assert lines[1].startswith("| Reaction system")


def test_legacy_five_cell_records_stay_readable():
    legacy = {"evidence_key": "E1", "paper": "P1", "object": "terminal allenes",
              "conditions": "CuBr, 1-alkynes, formaldehyde", "result": "formation", "units": ""}
    other = _new("P2", "E2", "allenes", "Pd catalyst", "THF, 50 C", "60% yield")
    section = _section([legacy, other])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    assert rows[0]["catalyst"] == ""                       # split cell absent
    assert rows[0]["conditions"] == "CuBr, 1-alkynes, formaldehyde"
    assert rows[0]["outcome"] == "formation"


def test_duplicate_records_collapse_to_one_row():
    record = _new("P1", "E1", "aldehydes", "CuBr", "DCM", "80% yield")
    section = _section([record, dict(record), _new("P2", "E2", "ketones", "Pd", "THF", "70% yield")])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    assert len(rows) == 2


def test_selectivity_only_result_leaves_no_bare_intensifier():
    section = _section([
        _new("P1", "E1", "allenols", "Cu/ligand", "DCM", "up to 97% ee"),
        _new("P2", "E2", "allenols", "Zn/ligand", "toluene", "up to 90% ee"),
    ])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    assert rows[0]["outcome"] == ""
    assert rows[0]["selectivity"] == "up to 97% ee"


def test_selectivity_range_is_kept_whole():
    section = _section([
        _new("P1", "E1", "allenols", "Cu", "DCM", "51-70% yields, 93-98% ee"),
        _new("P2", "E2", "allenols", "Zn", "toluene", "53-56% yields, 95-98% ee"),
    ])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    assert rows[0]["selectivity"] == "93-98% ee"
    assert rows[0]["outcome"] == "51-70% yields"


def test_selectivity_column_appears_only_for_asymmetric_chapters():
    section = _section([
        _new("P1", "E1", "allenols", "Cu/ligand", "DCM", "90% yield, 97% ee"),
        _new("P2", "E2", "allenols", "Zn/ligand", "toluene", "85% yield, 90% ee"),
    ])
    rendered = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1)
    assert "Selectivity" in rendered
    assert "97% ee" in rendered


def test_failure_cases_are_preserved_with_their_own_conditions():
    section = _section([
        _new("P1", "E1", "alkyne + aldehyde", "FeCl3", "", "no allene 3aa observed"),
        _new("P1", "E2", "alkyne + aldehyde", "MgI2", "", "not effective"),
        _new("P1", "E3", "alkyne + aldehyde", "CdI", "", "36% yield of allene 3aa"),
        _new("P2", "E4", "alkynes", "CuBr", "DCM", "80% yield"),
    ])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    outcomes = [row["outcome"] for row in rows]
    assert "36% yield of allene 3aa" in outcomes          # weak positive kept
    assert "no allene 3aa observed" in outcomes
    assert "not effective" in outcomes


def test_positive_no_phrases_are_not_dropped():
    section = _section([
        _new("P1", "E1", "allenol", "Cu", "DCM", "without racemization"),
        _new("P2", "E2", "allenol", "Pd", "THF", "no racemization observed"),
    ])
    rows = project_reaction_rows(section, {"P1": 1, "P2": 2})
    assert len(rows) == 2


def test_style_selection_prefers_quickref_only_for_multi_paper_body():
    body = {"section_role": "body", "primary_papers": ["P1", "P2"]}
    single = {"section_role": "body", "primary_papers": ["P1"]}
    intro = {"section_role": "introduction", "primary_papers": ["P1", "P2"]}
    assert select_table_style(body) == "reaction_quickref"
    assert select_table_style(single) == "comparison"
    assert select_table_style(intro) == "comparison"


def test_single_paper_section_does_not_emit_a_table():
    section = _section([_new("P1", "E1", "aldehydes", "CuBr", "DCM", "80% yield")])
    assert render_reaction_table(section, {"P1": 1}, table_number=1) == ""


def test_different_environments_and_transformations_stay_apart():
    section = _section([
        _new("P1", "E1", "aryl aldehydes", "CuBr/pyrrolidine", "DCM, rt", "90% yield"),
        _new("P1", "E2", "aryl aldehydes", "CuBr/pyrrolidine", "DCM, 40 C", "85% yield"),
        _new("P1", "E3", "ketones", "CuBr/pyrrolidine", "DCM, rt", "70% yield"),
        _new("P2", "E4", "aryl aldehydes", "Pd", "THF", "60% yield"),
    ])
    rows = group_reaction_rows(project_reaction_rows(section, {"P1": 1, "P2": 2}))
    p1 = [row for row in rows if row["paper"] == "P1"]
    assert len(p1) == 3
    assert all(row["method_records"] == 1 for row in p1)


def test_screened_catalysts_keep_their_associated_results():
    section = _section([
        _new("P1", "E1", "alkyne + aldehyde", "CdI", "", "36% yield"),
        _new("P1", "E2", "alkyne + aldehyde", "ZnI2", "", "slightly higher yield"),
        _new("P1", "E3", "alkyne + aldehyde", "Zn(OTf)", "", "19% NMR yield"),
        _new("P2", "E4", "ketones", "Rh", "THF", "60% yield"),
    ])
    rows = group_reaction_rows(project_reaction_rows(section, {"P1": 1, "P2": 2}))
    assert sum(1 for row in rows if row["paper"] == "P1") == 3
    row = next(r for r in rows if r["catalyst"] == "Zn(OTf)")
    assert row["outcome"] == "19% NMR yield"


def test_records_without_any_method_cell_never_merge():
    blank_a = {"evidence_key": "E1", "paper": "P1", "object": "a", "catalyst": "",
               "conditions": "", "environment": "", "result": "x", "units": ""}
    blank_b = {"evidence_key": "E2", "paper": "P1", "object": "b", "catalyst": "",
               "conditions": "", "environment": "", "result": "y", "units": ""}
    other = _new("P2", "E3", "c", "Pd", "THF", "70% yield")
    rows = group_reaction_rows(project_reaction_rows(_section([blank_a, blank_b, other]), {"P1": 1, "P2": 2}))
    assert sum(1 for row in rows if row["paper"] == "P1") == 2


def test_validation_blocks_a_row_without_outcome():
    rows = [
        {"paper": "P1", "transformation": "aldehydes", "outcome": "", "selectivity": "", "ref": "[1]"},
        {"paper": "P2", "transformation": "ketones", "outcome": "70% yield", "selectivity": "", "ref": "[2]"},
    ]
    assert "row_without_outcome" in validate_reaction_table(rows, list(CORE_COLUMNS))


def test_validation_blocks_a_single_paper_table():
    rows = [
        {"paper": "P1", "transformation": "a", "outcome": "80%", "selectivity": "", "ref": "[1]"},
        {"paper": "P1", "transformation": "b", "outcome": "70%", "selectivity": "", "ref": "[1]"},
    ]
    assert "too_few_papers" in validate_reaction_table(rows, list(CORE_COLUMNS))


def test_non_body_section_is_skipped():
    section = _section([
        _new("P1", "E1", "a", "Cu", "DCM", "80%"),
        _new("P2", "E2", "b", "Pd", "THF", "70%"),
    ], role="introduction")
    assert render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1) == ""


def test_empty_catalyst_column_is_dropped():
    section = _section([
        _new("P1", "E1", "a", "", "DCM, rt", "80% yield"),
        _new("P2", "E2", "b", "", "THF, 60 C", "70% yield"),
    ])
    rendered = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1)
    assert "Key catalyst system" not in rendered
    assert "Conditions / environment" in rendered


def test_single_selectivity_record_is_visible_including_qualifications():
    section = _section([
        _new("P1", "E1", "protected alcohols", "ZnBr2", "130 C, 11 h", "moderate yields with 96–99% ee or de after desilylation"),
        _new("P2", "E2", "alcohols", "CuBr2", "DCM", "70% yield"),
    ])
    visible = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1).split("<!--", 1)[0]
    assert "Selectivity" in visible
    assert "96–99% ee or de" in visible
    assert "after desilylation" in visible


def test_all_long_cells_and_units_survive_without_ellipsis():
    long = "sensitive substrate retaining both protecting groups under the stated conditions with the last essential qualification"
    a = _new("P1", "E1", long, long, long, "69")
    a["conditions"] = long
    a["units"] = "% yield"
    section = _section([a, _new("P2", "E2", "other substrate", "Pd", "THF", "70% yield")])
    visible = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1).split("<!--", 1)[0]
    assert visible.count(long) >= 3
    assert "69 % yield" in visible
    assert "…" not in visible


def test_numeric_ratio_and_units_only_metrics_are_not_lost():
    a = _new("P1", "E1", "substrate", "Cu", "THF", "80% yield")
    a["units"] = "97% ee"
    b = _new("P2", "E2", "substrate", "Pd", "THF", "70% yield, 2.5:1 dr")
    visible = render_reaction_table(_section([a, b]), {"P1": 1, "P2": 2}, table_number=1).split("<!--", 1)[0]
    assert "97% ee" in visible and "2.5:1 dr" in visible
    assert "80% yield" in visible and "70% yield" in visible


def test_only_exact_complementary_experiment_records_share_conditions():
    a = _new("P1", "E1", "substrate", "Cu", "THF, 25 C", "80% yield")
    b = _new("P1", "E1", "substrate", "", "", "80% yield")
    c = _new("P1", "E2", "different substrate", "", "", "60% yield")
    rows = project_reaction_rows(_section([a, b, c]), {"P1": 1})
    assert len(rows) == 2
    assert rows[0]["catalyst"] == "Cu"
    assert rows[1]["catalyst"] == "" and rows[1]["conditions"] == ""


def test_conflicting_shared_records_do_not_fill_blank_conditions():
    a = _new("P1", "E1", "substrate", "Cu", "THF, 25 C", "80% yield")
    b = _new("P1", "E1", "substrate", "Pd", "DCM, 40 C", "80% yield")
    c = _new("P1", "E1", "substrate", "", "", "80% yield")
    rows = project_reaction_rows(_section([a, b, c]), {"P1": 1})
    assert rows[2]["catalyst"] == "" and rows[2]["conditions"] == ""


def test_grouped_examples_keep_yield_selectivity_pairs():
    a = _new("P1", "E1", "substrate family", "Cu", "THF, 25 C", "90% yield, 80% ee")
    b = _new("P1", "E2", "substrate family", "Cu", "THF, 25 C", "60% yield, 99% ee")
    section = _section([a, b, _new("P2", "E3", "other", "Pd", "DCM", "70% yield")])
    visible = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1).split("<!--", 1)[0]
    assert "(1) 90% yield; (2) 60% yield" in visible
    assert "(1) 80% ee; (2) 99% ee" in visible
    assert "combined best-case" in visible


def test_ambiguous_or_unknown_evidence_never_uses_first_paper():
    section = _section([_new("P1", "E1", "a", "Cu", "DCM", "80%"), _new("P2", "E2", "b", "Pd", "THF", "70%")])
    section["paragraphs"][0]["claim_realizations"][0]["result_context"] = [
        {"object": "unbound", "result": "99%"},
        {"evidence_key": "not-shown", "object": "unknown", "result": "99%"}]
    assert project_reaction_rows(section, {"P1": 1, "P2": 2}) == []


def test_markdown_pipes_cannot_add_columns_and_missing_fields_do_not_claim_absence():
    section = _section([_new("P1", "E1", "a | b", "", "", "80%"), _new("P2", "E2", "c", "Pd", "THF", "70%")])
    visible = render_reaction_table(section, {"P1": 1, "P2": 2}, table_number=1).split("<!--", 1)[0]
    assert r"a \| b" in visible
    assert "does not establish absence from the original paper" in visible
    assert "not reported" not in visible
