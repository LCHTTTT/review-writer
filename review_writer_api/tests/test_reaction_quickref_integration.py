"""End-to-end: the switch selects the condensed reader table during assembly."""

import unittest
from pathlib import Path

from review_writer_api.domain_services.drafts import DraftsService
from review_writer_core.manuscript_state import build_manuscript_state
from review_writer_core.latex_renderer import render_tex


def _section_index():
    context = [
        {"evidence_key": "E1", "object": "propargylic alcohols + aldehydes",
         "catalyst": "CuBr, pyrrolidine", "conditions": "", "environment": "DCM, rt",
         "result": "92% yield", "units": ""},
        {"evidence_key": "E2", "object": "terminal alkynes + ketones",
         "catalyst": "chiral phosphoric acid", "conditions": "",
         "environment": "toluene, 60 C", "result": "80% yield", "units": ""},
    ]
    refs = [
        {"evidence_key": "E1", "paper_id": "P1", "quote": "P1 passage"},
        {"evidence_key": "E2", "paper_id": "P2", "quote": "P2 passage"},
    ]
    section = {
        "section_id": "S01",
        "heading": "Aldehydes",
        "heading_level": 2,
        "section_role": "body",
        "primary_papers": ["P1", "P2"],
        "paragraphs": [{
            "paragraph_id": "S01-p1",
            "text": "The two systems differ in catalyst and environment.",
            "cited_paper_ids": ["P1", "P2"],
            "claim_realizations": [{
                "claim_id": "c1",
                "citation_group": ["P1", "P2"],
                "evidence_refs": refs,
                "result_context": context,
            }],
        }],
    }
    return {"sections": [section]}


class ReactionQuickrefIntegrationTests(unittest.TestCase):
    def _assemble(self, *, enabled):
        return DraftsService._assemble_markdown(
            "Review", _section_index(), {}, {"rows": []}, None,
            reaction_quickref_tables=enabled,
        )

    def test_switch_on_emits_the_condensed_reader_table(self):
        markdown = self._assemble(enabled=True)
        self.assertIn("<!-- reaction_table:", markdown)
        self.assertIn("Reaction system / transformation", markdown)
        self.assertIn("Conditions / environment", markdown)

    def test_switch_off_keeps_the_legacy_renderer(self):
        markdown = self._assemble(enabled=False)
        self.assertNotIn("<!-- reaction_table:", markdown)

    def test_assembly_and_latex_keep_full_conditions_and_one_selectivity_row(self):
        index = _section_index()
        records = index["sections"][0]["paragraphs"][0]["claim_realizations"][0]["result_context"]
        records[0]["environment"] = "THF, 130 C, 11 h, then deprotection for 2 h at room temperature"
        records[0]["result"] = "62% combined yield over two steps, 99% ee"
        markdown = DraftsService._assemble_markdown("Review", index, {}, {"rows": []}, None, reaction_quickref_tables=True)
        state = build_manuscript_state(markdown)
        table = next(block for block in state["blocks"] if block["kind"] == "table")
        self.assertIn("Selectivity", table["header"])
        self.assertIn(records[0]["environment"], " ".join(table["rows"][0]))
        template = Path(__file__).resolve().parents[2] / "review_writer_core/resources/pdf/modern-survey.tex"
        tex = render_tex(state, profile="en", template=template.read_text(encoding="utf-8"))
        self.assertIn(records[0]["environment"], tex)
        self.assertIn(r"99\% ee", tex)
        self.assertNotIn("…", tex)

    def test_large_table_uses_existing_pagination_without_cutting_cells(self):
        index = _section_index()
        record = index["sections"][0]["paragraphs"][0]["claim_realizations"][0]["result_context"][0]
        record["environment"] = ("complete experimental detail " * 200) + "FINAL_ESSENTIAL_DETAIL"
        markdown = DraftsService._assemble_markdown("Review", index, {}, {"rows": []}, None, reaction_quickref_tables=True)
        state = build_manuscript_state(markdown)
        template = Path(__file__).resolve().parents[2] / "review_writer_core/resources/pdf/modern-survey.tex"
        tex = render_tex(state, profile="en", template=template.read_text(encoding="utf-8"))
        self.assertGreater(tex.count(r"\begin{table*}"), 1)
        self.assertIn(r"FINAL\_ESSENTIAL\_DETAIL", tex)
        # Page boundaries can fall between words, but every token survives.
        for token in ("complete", "experimental", "detail"):
            self.assertEqual(200, tex.count(token))


if __name__ == "__main__":
    unittest.main()
