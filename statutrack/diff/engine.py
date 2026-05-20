"""Section-level diff engine.

Given two consecutive versions of one instrument, the engine produces
one ``Diff`` per section describing how that section changed (or
didn't) and, for sections that were modified, the inline HTML the
template renders verbatim using the ``<ins>`` / ``<del>`` track-changes
styling defined in ``base.html``.

Design notes
------------

Alignment is done by (in priority order):

1. **LIMS fid.** Justice Canada assigns a stable ``lims:fid`` to each
   provision; when present on both sides it is the cheapest and most
   trustworthy alignment key.

2. **Citation.** Falls back to the human-readable citation string
   ("s. 71(1)", "s. 71(1)(a)"). Sections that haven't been renumbered
   land here.

3. **Renumbered-section detector.** Any leftover unmatched sections
   from each side are pair-wise compared with
   :func:`difflib.SequenceMatcher.ratio`; pairs whose content
   similarity exceeds a tunable threshold (default 0.75) are marked as
   *renumbered* — same content, different citation. Pairs below the
   threshold are emitted as separate add/remove rows.

Diff rendering is word-level via :func:`difflib.SequenceMatcher`,
producing HTML strings shaped like
``"… for a period of <del>five years</del><ins>six years</ins> from …"``.
The output is safe to drop straight into a Jinja template with
``|safe`` — the only HTML produced are the two literal tags, and the
input text is HTML-escaped beforehand so embedded ``<`` characters in
the source can't break out.
"""
from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass
from typing import Literal

ChangeType = Literal["added", "removed", "modified", "renumbered", "unchanged"]

RENUMBER_THRESHOLD = 0.75


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionSnapshot:
    """The fields the diff engine needs about a single section row.

    Decoupled from :class:`statutrack.parser.ProvisionRow` and from the
    SQLite row mapper so the engine can be exercised in isolation by
    tests without dragging the whole parser or loader into scope.
    """
    citation: str
    fid: str
    content: str
    ord: int = 0


@dataclass(frozen=True)
class Diff:
    """One section's worth of change between two versions."""
    change_type: ChangeType
    old: SectionSnapshot | None
    new: SectionSnapshot | None
    inline_html: str

    @property
    def citation(self) -> str:
        """The citation used to address this diff in URLs / headings."""
        if self.new is not None:
            return self.new.citation
        if self.old is not None:
            return self.old.citation
        return ""


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def _index_by(key_fn, sections):
    """Return ``{key: section}`` for the subset of ``sections`` whose
    key is non-empty and unique. Duplicates land in a separate bucket
    so the caller can fall back to a different alignment strategy for
    them."""
    seen: dict[str, SectionSnapshot] = {}
    duplicates: set[str] = set()
    for s in sections:
        k = key_fn(s)
        if not k:
            continue
        if k in seen:
            duplicates.add(k)
        else:
            seen[k] = s
    return {k: v for k, v in seen.items() if k not in duplicates}, duplicates


# Word-level tokenizer used for both similarity scoring and the inline
# diff itself. Splitting on word boundaries keeps the rendered output
# legible — whitespace and punctuation are preserved as separate
# tokens, so the diff lands on word edges rather than mid-character.
_WORD_RE = re.compile(r"(\s+|\w+|[^\w\s])", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text) if t != ""]


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    sm = difflib.SequenceMatcher(a=_tokenize(a), b=_tokenize(b),
                                 autojunk=False)
    return sm.ratio()


# ---------------------------------------------------------------------------
# Inline HTML rendering
# ---------------------------------------------------------------------------

def render_inline(old_text: str, new_text: str) -> str:
    """Produce inline diff HTML for two strings.

    Both inputs are HTML-escaped before any tag is emitted, so embedded
    ``<`` characters in the regulatory text never break the markup
    (regulations do contain literal ``<`` in formulas occasionally).
    """
    a = _tokenize(old_text)
    b = _tokenize(new_text)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    parts: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            parts.append(html.escape("".join(a[i1:i2])))
        elif op == "delete":
            parts.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
        elif op == "insert":
            parts.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
        elif op == "replace":
            parts.append(f"<del>{html.escape(''.join(a[i1:i2]))}</del>")
            parts.append(f"<ins>{html.escape(''.join(b[j1:j2]))}</ins>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Top-level diff
# ---------------------------------------------------------------------------

def diff_versions(old_sections: list[SectionSnapshot],
                  new_sections: list[SectionSnapshot],
                  *,
                  renumber_threshold: float = RENUMBER_THRESHOLD) -> list[Diff]:
    """Diff two ordered lists of sections, returning one ``Diff`` per
    aligned pair plus one ``Diff`` per unmatched section on either side.

    The returned list preserves the order of ``new_sections`` for
    aligned + insert + renumbered diffs (so a rendered diff view reads
    in the new version's flow), with removals appended at the end so
    the reader can see what disappeared in one block.
    """
    # Build lookup tables. Sections whose fid is empty fall back to a
    # citation-only match, which is the common case for older
    # provisions that pre-date the LIMS fid scheme.
    old_by_fid, old_fid_dups = _index_by(lambda s: s.fid, old_sections)
    new_by_fid, new_fid_dups = _index_by(lambda s: s.fid, new_sections)
    old_by_cit, _ = _index_by(lambda s: s.citation, old_sections)
    new_by_cit, _ = _index_by(lambda s: s.citation, new_sections)

    matched_old_idx: set[int] = set()
    matched_new_idx: set[int] = set()
    by_idx_old = {id(s): i for i, s in enumerate(old_sections)}
    by_idx_new = {id(s): i for i, s in enumerate(new_sections)}

    aligned: list[tuple[SectionSnapshot, SectionSnapshot, ChangeType]] = []

    # --- Pass 1: align by fid (when both sides have one).
    for fid, n in new_by_fid.items():
        o = old_by_fid.get(fid)
        if o is None:
            continue
        matched_old_idx.add(by_idx_old[id(o)])
        matched_new_idx.add(by_idx_new[id(n)])
        # Same fid + citation change is a renumbering, regardless of
        # whether the content also drifted. Same fid + same citation +
        # same content is unchanged. Everything else is modified.
        if o.citation != n.citation:
            ct: ChangeType = "renumbered"
        elif o.content == n.content:
            ct = "unchanged"
        else:
            ct = "modified"
        aligned.append((o, n, ct))

    # --- Pass 2: align by citation for any sections not matched by fid.
    for cit, n in new_by_cit.items():
        if by_idx_new[id(n)] in matched_new_idx:
            continue
        o = old_by_cit.get(cit)
        if o is None or by_idx_old[id(o)] in matched_old_idx:
            continue
        matched_old_idx.add(by_idx_old[id(o)])
        matched_new_idx.add(by_idx_new[id(n)])
        ct = "unchanged" if o.content == n.content else "modified"
        aligned.append((o, n, ct))

    # --- Pass 3: renumbered-section detection.
    unmatched_old = [s for i, s in enumerate(old_sections) if i not in matched_old_idx]
    unmatched_new = [s for i, s in enumerate(new_sections) if i not in matched_new_idx]
    for n in list(unmatched_new):
        best_o = None
        best_score = renumber_threshold
        for o in unmatched_old:
            score = _similarity(o.content, n.content)
            if score > best_score:
                best_score = score
                best_o = o
        if best_o is not None:
            aligned.append((best_o, n, "renumbered"))
            unmatched_old.remove(best_o)
            unmatched_new.remove(n)

    # --- Emit diffs in a stable order: new-flow first (alignments and
    # insertions), then deletions at the end so they're visible in one
    # block at the bottom of the rendered view.
    new_flow_index = {id(n): n.ord for n in new_sections}
    aligned.sort(key=lambda t: new_flow_index.get(id(t[1]), 0))

    diffs: list[Diff] = []
    for o, n, ct in aligned:
        if ct == "unchanged":
            inline = html.escape(n.content)
        else:
            inline = render_inline(o.content, n.content)
        diffs.append(Diff(change_type=ct, old=o, new=n, inline_html=inline))

    for n in unmatched_new:
        diffs.append(Diff(
            change_type="added",
            old=None,
            new=n,
            inline_html=f"<ins>{html.escape(n.content)}</ins>",
        ))

    for o in unmatched_old:
        diffs.append(Diff(
            change_type="removed",
            old=o,
            new=None,
            inline_html=f"<del>{html.escape(o.content)}</del>",
        ))

    return diffs
