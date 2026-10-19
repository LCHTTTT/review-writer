from review_writer_core.stages.sections.authoring import style_findings
from review_writer_core.publication_voice import publication_voice_issues


def claim(text, paper, quote="The reaction afforded the product."):
    return {"claim_id": "C1", "paragraph_id": "S1-p1", "claim": text,
            "citation_group": [paper], "evidence_refs": [{"paper_id": paper, "evidence_key": "E1", "quote": quote}]}


def test_registered_full_id_and_unique_prefix_are_editorial_findings():
    pid = "P5021405023598897354"
    for label in (pid, "P502"):
        assert style_findings([claim(label + " described the reaction.", pid)])["C1"] == ["internal_paper_identifier"]


def test_real_source_compound_labels_and_quoted_text_are_not_paper_leaks():
    pid = "P5021405023598897354"
    assert not style_findings([claim("Ligand P502 gave the product.", pid, "Ligand P502 gave the product.")])
    assert not style_findings([claim('The label "P502" identifies the compound.', pid)])
    assert not style_findings([claim("Ligand P1 and protein p53 were compared.", pid)])


def test_ambiguous_short_prefix_is_not_assigned_to_the_first_paper():
    rows = [claim("P502 described the reaction.", "P5021111111111111111"),
            {**claim("Another reaction was compared.", "P5022222222222222222"), "claim_id": "C2"}]
    assert "internal_paper_identifier" not in style_findings(rows).get("C1", [])


def test_exact_registered_id_takes_precedence_over_longer_prefix_matches():
    rows = [claim("P502 described the reaction.", "P502"),
            {**claim("Another reaction was compared.", "P5022222222222222222"), "claim_id": "C2"}]
    assert style_findings(rows)["C1"] == ["internal_paper_identifier"]


def test_publication_check_reports_ids_only_in_prose_not_source_metadata():
    pid = "P5021405023598897354"
    text = 'P502 described the reaction. [1]\n\n<!-- citation_map: {"1":"' + pid + '"} -->\n\n`P502`\n\n![P502](/image)\n\n## References\n[1] P502 is part of an article title.'
    rows = publication_voice_issues(text, paper_ids=[pid])
    assert [(r["code"], r["phrase"]) for r in rows] == [("internal_paper_identifier", "P502")]
    assert text.startswith("P502 described")
    assert not publication_voice_issues("P502 described the reaction.")
