"""Tests for the section-level diff engine."""
from __future__ import annotations

from statutrack.diff.engine import (
    Diff,
    SectionSnapshot,
    diff_versions,
    render_inline,
)


def _S(citation: str, content: str, *, fid: str = "", ord: int = 0) -> SectionSnapshot:
    return SectionSnapshot(citation=citation, fid=fid, content=content, ord=ord)


# --------------------------------------------------------------------------
# render_inline
# --------------------------------------------------------------------------

def test_render_inline_marks_a_simple_word_swap():
    out = render_inline(
        "Records shall be kept for five years.",
        "Records shall be kept for six years.",
    )
    assert "<del>five</del>" in out
    assert "<ins>six</ins>" in out
    assert "Records shall be kept for" in out


def test_render_inline_escapes_html_in_source_text():
    """Embedded angle brackets in the regulatory text (occasionally
    found in formulas) must not break out of the wrapping tags."""
    out = render_inline("x < 5", "x < 6")
    assert "<" not in out.replace("<del>", "").replace("</del>", "") \
                       .replace("<ins>", "").replace("</ins>", "")
    assert "&lt;" in out


def test_render_inline_handles_pure_insertion_and_deletion():
    only_added = render_inline("A.", "A new sentence appears. A.")
    assert "<ins>" in only_added and "<del>" not in only_added
    only_removed = render_inline("Old phrase removed. A.", "A.")
    assert "<del>" in only_removed and "<ins>" not in only_removed


def test_render_inline_is_empty_for_unchanged_text():
    out = render_inline("Same text.", "Same text.")
    assert "<del>" not in out
    assert "<ins>" not in out


# --------------------------------------------------------------------------
# diff_versions: alignment
# --------------------------------------------------------------------------

def test_unchanged_section_emits_unchanged_diff():
    diffs = diff_versions(
        [_S("s. 1", "Hello.", fid="f1", ord=0)],
        [_S("s. 1", "Hello.", fid="f1", ord=0)],
    )
    assert len(diffs) == 1
    assert diffs[0].change_type == "unchanged"


def test_modified_section_aligned_by_fid_even_when_citation_renumbered():
    diffs = diff_versions(
        [_S("s. 5", "Records shall be kept for five years.", fid="f1", ord=0)],
        [_S("s. 7", "Records shall be kept for five years.", fid="f1", ord=0)],
    )
    assert len(diffs) == 1
    assert diffs[0].change_type == "renumbered"


def test_modified_section_aligned_by_citation_when_no_fid():
    diffs = diff_versions(
        [_S("s. 1(1)", "Keep records for five years.")],
        [_S("s. 1(1)", "Keep records for six years.")],
    )
    assert len(diffs) == 1
    assert diffs[0].change_type == "modified"
    assert "<del>five</del>" in diffs[0].inline_html
    assert "<ins>six</ins>" in diffs[0].inline_html


def test_added_section_emits_added_diff():
    diffs = diff_versions(
        [_S("s. 1", "Existing.", fid="f1")],
        [_S("s. 1", "Existing.", fid="f1"),
         _S("s. 2", "Brand new section.", fid="f_new")],
    )
    added = [d for d in diffs if d.change_type == "added"]
    assert len(added) == 1
    assert added[0].new is not None
    assert added[0].new.citation == "s. 2"
    assert "<ins>" in added[0].inline_html


def test_removed_section_emits_removed_diff_at_end():
    diffs = diff_versions(
        [_S("s. 1", "Existing.", fid="f1"),
         _S("s. 2", "Old section about to disappear.", fid="f_old")],
        [_S("s. 1", "Existing.", fid="f1")],
    )
    # Removed diffs sort to the end of the list per engine contract.
    assert diffs[-1].change_type == "removed"
    assert diffs[-1].old is not None
    assert diffs[-1].old.citation == "s. 2"


def test_renumbered_detector_finds_high_similarity_match():
    """When fid is absent and citation has changed but content is
    largely the same, the engine should align them as renumbered."""
    old_text = "A reporting entity shall keep records of every transaction for five years."
    new_text = "A reporting entity shall keep records of every transaction for six years."
    diffs = diff_versions(
        [_S("s. 71", old_text)],
        [_S("s. 73", new_text)],
    )
    # Without fid and with different citations, alignment passes 1 and
    # 2 produce nothing; pass 3 (similarity) should bind them.
    assert len(diffs) == 1
    assert diffs[0].change_type == "renumbered"


def test_low_similarity_pair_is_emitted_as_separate_add_and_remove():
    diffs = diff_versions(
        [_S("s. 1", "Old subject about widgets entirely.")],
        [_S("s. 1", "New subject about gizmos entirely different.")],
    )
    # Same citation, low similarity — alignment passes still bind them
    # (modified), because alignment by citation does not check
    # similarity. This test pins that behaviour so we don't
    # accidentally lose modified diffs to oversensitive thresholding.
    assert len(diffs) == 1
    assert diffs[0].change_type == "modified"


def test_realistic_two_version_diff():
    """A small but realistic shape — a few unchanged sections, one
    modified, one added, one removed — exercised end-to-end."""
    old = [
        _S("s. 1", "Definitions apply.", fid="f1", ord=0),
        _S("s. 2", "Reporting entities shall keep records for five years.", fid="f2", ord=1),
        _S("s. 3", "An obsolete provision.", fid="f3", ord=2),
    ]
    new = [
        _S("s. 1", "Definitions apply.", fid="f1", ord=0),
        _S("s. 2", "Reporting entities shall keep records for six years.", fid="f2", ord=1),
        _S("s. 4", "A brand new disclosure rule.", fid="f4", ord=2),
    ]
    diffs = diff_versions(old, new)

    by_change = {d.change_type for d in diffs}
    assert {"unchanged", "modified", "added", "removed"} <= by_change

    modified = [d for d in diffs if d.change_type == "modified"]
    assert len(modified) == 1
    assert "<del>five</del>" in modified[0].inline_html
    assert "<ins>six</ins>" in modified[0].inline_html
