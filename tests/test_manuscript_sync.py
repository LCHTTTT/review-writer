from review_writer_core.manuscript_sync import merge_prose
import pytest


def test_disjoint_upstream_and_manual_edits_preserve_both():
    value = merge_prose("a\nb\nc\n", "A\nb\nc\n", "a\nb\nC\n")
    assert value["text"] == "A\nb\nC\n"
    assert value["complete"] and value["applied_count"] == 1


def test_same_line_conflict_keeps_manual_text_and_reports_upstream():
    value = merge_prose("old\n", "new\n", "manual\n")
    assert value["text"] == "manual\n"
    assert not value["complete"]
    assert value["conflicts"][0]["upstream_text"] == "new\n"


def test_identical_change_is_not_double_applied():
    assert merge_prose("a\n", "A\n", "A\n")["text"] == "A\n"
    assert merge_prose("a\n", "A\n", "A\n")["complete"]


def test_competing_insertions_are_conflicts_not_silently_concatenated():
    value = merge_prose("a\n", "new\na\n", "manual\na\n")
    assert value["text"] == "manual\na\n" and not value["complete"]


def test_delete_and_manual_edit_overlap_preserves_manual():
    value = merge_prose("a\nb\n", "a\n", "a\nB\n")
    assert value["text"] == "a\nB\n" and not value["complete"]


def test_unknown_baseline_not_coerced_to_empty():
    with pytest.raises(ValueError):
        merge_prose(None, "new", "manual")


def test_manual_deletion_is_retained_while_other_upstream_change_applies():
    assert merge_prose("a\nb\nc\n", "a\nb\nC\n", "a\nc\n")["text"] == "a\nC\n"
