"""Reader-facing section reaction quick-reference table.

Consumes the shared experiment note (``result_context``) that source writing
already records, so prose and this table never re-read the papers separately.

The pipeline is:

    project  -> one row per checked experiment note (de-duplicated)
    group    -> one row per independently adoptable method
    validate -> reader acceptance gate (no table rather than a bad table)
    render   -> condensed columns + optional selectivity column
"""

from __future__ import annotations

import json
import re
from typing import Any

from .draft_bibliography import format_citation_group
from .publication_tables import _cell_text, _result_with_units

# Core reader columns (condensed). "conditions" and "environment" merge into one
# column; the substrate class already lives inside the transformation.
CORE_COLUMNS = ("transformation", "catalyst", "conditions", "outcome")
SELECTIVITY_COLUMN = "selectivity"
HEADERS = {
    "transformation": "Reaction system / transformation",
    "catalyst": "Key catalyst system",
    "conditions": "Conditions / environment",
    "outcome": "Outcome",
    "selectivity": "Selectivity",
}
_KNOWN_COLUMNS = set(CORE_COLUMNS) | {SELECTIVITY_COLUMN}

# Match a selectivity value or range (e.g. "97% ee", "93-98% ee", "90–96% de").
_SELECTIVITY_VALUE = re.compile(
    r"(?:(?:up to|at least|at most|about|ca\.)\s+|[<>≤≥]\s*)?"
    r"\d+(?:\.\d+)?(?:\s*[–\-]\s*\d+(?:\.\d+)?)?"
    r"(?:\s*:\s*\d+(?:\.\d+)?)?\s*%?\s*(?:ee|er|dr|de|rr|es)\b"
    r"(?:\s+(?:or|and)\s+(?:ee|er|dr|de|rr|es)\b)?", re.I)
_EMPTY = "—"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe_join(*values: Any) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = _clean(value).strip(" ;,.")
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            ordered.append(text)
    return "; ".join(ordered)


def _split_outcome(result: Any, units: Any) -> tuple[str, str]:
    """Separate selectivity metrics from the remaining outcome."""
    text = _result_with_units(result, units) if _clean(result) else _clean(units)
    selectivity = list(dict.fromkeys(match.group(0) for match in _SELECTIVITY_VALUE.finditer(text)))
    # Move only standalone trailing metrics. Embedded substrate qualifiers or
    # correlations stay intact in Outcome rather than becoming dangling prose.
    remainder = text
    if _SELECTIVITY_VALUE.fullmatch(text):
        remainder = ""
    else:
        suffix = re.search(r"[,;]\s*(" + _SELECTIVITY_VALUE.pattern + r")\s*$", text, re.I)
        if suffix:
            remainder = text[:suffix.start()].strip(" ;,.")
    return _clean(remainder), "; ".join(selectivity)


def project_reaction_rows(section: dict[str, Any], citation_numbers: dict[str, int]) -> list[dict[str, Any]]:
    """Project checked experiment notes into de-duplicated reaction rows.

    Unsupported or unbound records are skipped rather than guessed. Legacy
    five-cell records (no catalyst/environment) stay readable.
    """
    rows: list[dict[str, Any]] = []
    for paragraph in section.get("paragraphs") or []:
        if not isinstance(paragraph, dict):
            continue
        for claim in paragraph.get("claim_realizations") or []:
            if not isinstance(claim, dict):
                continue
            key_to_paper = {
                str(ref.get("evidence_key")): str(ref.get("paper_id") or "")
                for ref in claim.get("evidence_refs") or []
                if isinstance(ref, dict)
            }
            papers = list(dict.fromkeys(claim.get("citation_group") or []))
            fallback = str(papers[0]) if len(papers) == 1 else ""
            for record in claim.get("result_context") or []:
                if not isinstance(record, dict):
                    continue
                key = _clean(record.get("evidence_key"))
                paper = key_to_paper.get(key) or (fallback if not key else "")
                if not paper or paper not in citation_numbers:
                    continue
                transformation = _clean(record.get("object"))
                outcome_main, selectivity = _split_outcome(record.get("result"), record.get("units"))
                if not transformation or not outcome_main and not selectivity:
                    continue
                row = {
                    "paper": paper,
                    "transformation": transformation,
                    "catalyst": _clean(record.get("catalyst")),
                    "conditions": _dedupe_join(record.get("conditions"), record.get("environment")),
                    "environment": _clean(record.get("environment")),
                    "outcome": outcome_main,
                    "selectivity": selectivity,
                    "group": _clean(record.get("group")),
                    "ref": format_citation_group([citation_numbers[paper]]),
                    "evidence_key": _clean(record.get("evidence_key")),
                }
                rows.append(row)
    # Fill only complementary records of the SAME experiment: identical source,
    # object and outcome. A common paper or table alone is not a scope contract.
    for row in rows:
        if not row["evidence_key"]:
            continue
        peers = [other for other in rows if all(row[k] == other[k] for k in
                 ("paper", "evidence_key", "transformation", "outcome", "selectivity"))]
        if any(len({other[column] for other in peers if other[column]}) > 1
               for column in ("catalyst", "conditions", "environment")):
            continue
        for column in ("catalyst", "conditions", "environment"):
            values = list(dict.fromkeys(other[column] for other in peers if other[column]))
            if not row[column] and len(values) == 1:
                row[column] = values[0]
    unique = {}
    for row in rows:
        identity = tuple(row[k] for k in ("paper", "transformation", "catalyst", "conditions", "outcome", "selectivity"))
        unique.setdefault(identity, row)
    return list(unique.values())


def _merge_cell(members: list[dict[str, Any]], column: str) -> str:
    values = [value for value in dict.fromkeys(_clean(m.get(column)) for m in members) if value]
    if not values:
        return ""
    return "; ".join(values)


def group_reaction_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group only identical known methods; keep each example/result pair.

    No method family is inferred from a paper ID, and different conditions stay
    separate. Grouped result and selectivity cells share numbered examples.
    """
    from collections import OrderedDict

    groups: "OrderedDict[tuple, list[dict[str, Any]]]" = OrderedDict()
    for index, row in enumerate(rows):
        method = tuple(_clean(row.get(k)) for k in ("catalyst", "conditions", "environment", "group"))
        # Known identical methods may summarize multiple substrate examples.
        # Sparse records and different conditions stay separate; never form a
        # synthetic best yield/ee pair from independently joined cells.
        key = (row["paper"], row["transformation"], *method) if method[0] and method[1] else (row["paper"], index)
        groups.setdefault(key, []).append(row)
    merged: list[dict[str, Any]] = []
    for members in groups.values():
        numbered = len(members) > 1
        def paired(column):
            if not numbered:
                return _clean(members[0].get(column))
            return "; ".join(f"({i}) {_clean(m.get(column)) or _EMPTY}" for i, m in enumerate(members, 1))
        merged.append({
            "paper": members[0]["paper"],
            "transformation": paired("transformation"),
            "catalyst": _merge_cell(members, "catalyst"),
            "conditions": _merge_cell(members, "conditions"),
            "outcome": paired("outcome"),
            "selectivity": paired("selectivity") if any(m.get("selectivity") for m in members) else "",
            "group": members[0]["group"],
            "ref": members[0]["ref"],
            "method_records": len(members),
            "experiments": [{k: m.get(k, "") for k in ("transformation", "catalyst", "conditions", "outcome", "selectivity", "evidence_key")} for m in members],
        })
    return merged


def validate_reaction_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    """Reader acceptance gate: return the reasons a table must not be published."""
    issues: list[str] = []
    if set(columns) - _KNOWN_COLUMNS:
        issues.append("unknown_reader_column")
    if len(rows) < 2:
        issues.append("too_few_rows")
    if len({row["paper"] for row in rows}) < 2:
        issues.append("too_few_papers")
    for row in rows:
        if not _clean(row.get("transformation")):
            issues.append("row_without_transformation")
            break
    for row in rows:
        if not _clean(row.get("outcome")) and not _clean(row.get("selectivity")):
            issues.append("row_without_outcome")
            break
    for row in rows:
        if not _clean(row.get("ref")):
            issues.append("row_without_reference")
            break
    return list(dict.fromkeys(issues))


def build_table(
    section: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    table_number: int,
) -> dict[str, Any] | None:
    """Select columns, validate, and assemble the reader table."""
    rows = group_reaction_rows(rows)
    columns = list(CORE_COLUMNS)
    # Even one reported metric must remain visible.
    if any(row["selectivity"] for row in rows):
        columns.append(SELECTIVITY_COLUMN)
    # Drop a core column that is empty for every row.
    for column in ("catalyst", "conditions"):
        if all(not _clean(row.get(column)) for row in rows):
            columns.remove(column)
    if sum(1 for column in columns if any(_clean(row.get(column)) for row in rows)) < 2:
        return None
    issues = validate_reaction_table(rows, columns)
    if issues:
        return None
    heading = _clean(section.get("heading") or section.get("title")) or "this section"
    header = [HEADERS[column] for column in columns] + ["Ref."]
    table_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    provenance: list[dict[str, Any]] = []
    empty_used = False
    for row in rows:
        cells: list[str] = []
        for column in columns:
            value = _cell_text(row.get(column))
            if not value:
                value = _EMPTY
                empty_used = True
            cells.append(value)
        cells.append(row["ref"])
        table_lines.append("| " + " | ".join(cells) + " |")
        provenance.append({"paper_id": row["paper"], "ref": row["ref"],
                           "method_records": row.get("method_records", 1),
                           "experiments": row["experiments"],
                           **{column: _clean(row.get(column)) for column in columns}})
    # The caption must sit immediately before the table so the manuscript
    # parser binds it to the table (and LaTeX numbers it as "Table N"). The
    # legend goes after the table and stays a normal paragraph.
    lines = [f"Table {table_number}. {heading}.", "", *table_lines]
    if empty_used:
        lines += ["", f"{_EMPTY} = unavailable in the extracted records; this does not establish absence from the original paper."]
    if any(row["method_records"] > 1 for row in rows):
        lines += ["", "Numbered entries within a row refer to the same substrate/example across columns; results are not combined best-case values."]
    metadata = json.dumps(
        {
            "section_id": section.get("section_id"),
            "projection": "section-reaction-table/3",
            "columns": columns,
            "rows": provenance,
            "row_count": len(rows),
            "validation": "passed",
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return {
        "markdown": "\n".join(lines) + "\n\n<!-- reaction_table: " + metadata + " -->",
        "columns": columns,
        "rows": provenance,
    }


def select_table_style(section: dict[str, Any]) -> str:
    """Choose the reader-table renderer for one section.

    Body chapters that compare at least two primary papers get the condensed
    quick-reference table; everything else keeps the legacy fact comparison.
    """
    if str(section.get("section_role") or "body").casefold() != "body":
        return "comparison"
    primary = {str(paper) for paper in section.get("primary_papers") or [] if str(paper)}
    if len(primary) < 2:
        return "comparison"
    return "reaction_quickref"


def render_reaction_table(
    section: dict[str, Any],
    citation_numbers: dict[str, int],
    *,
    table_number: int,
) -> str:
    """Render the condensed reader table, or "" when the section cannot support one."""
    if str(section.get("section_role") or "body").casefold() != "body":
        return ""
    result = build_table(section, project_reaction_rows(section, citation_numbers), table_number=table_number)
    return result["markdown"] if result else ""
