"""End-to-end checks on the Phase 1 ``statutrack.parser`` public API.

These tests exercise the parsing chain through the small synthetic
regulation fixture rather than the full Justice Canada XML — the
fixture is fast, deterministic, and stays committed alongside the
test. Real-XML fidelity is already covered by manual runs of the
``laws_xml_to_excel`` CLI and will gain regression coverage in Phase 2
once the ingest harness is in place.
"""
from __future__ import annotations

from pathlib import Path

from statutrack.parser import ParsedDocument, parse_document

FIXTURE = Path(__file__).parent / "fixtures" / "sample_regulation.xml"


def test_parse_document_returns_parseddocument():
    parsed = parse_document(FIXTURE)
    assert isinstance(parsed, ParsedDocument)


def test_regulation_identification_detected():
    parsed = parse_document(FIXTURE)
    assert parsed.document_type == "regulation"
    assert parsed.long_title == "Regulations Respecting Synthetic Compliance"
    assert parsed.short_title == "Sample Compliance Regulations"
    assert parsed.instrument_number == "SOR/2099-001"


def test_sections_extracted_with_marginal_notes():
    parsed = parse_document(FIXTURE)
    labels = [s.section for s in parsed.sections]
    assert labels == ["1", "2"]
    marginals = {s.section: s.marginal_note for s in parsed.sections}
    assert marginals["1"] == "Definitions"
    assert marginals["2"] == "Records"


def test_provisions_include_subsections_and_paragraphs():
    parsed = parse_document(FIXTURE)
    levels = {p.level for p in parsed.provisions}
    assert {"Section", "Subsection", "Paragraph"} <= levels


def test_definitions_extracted():
    parsed = parse_document(FIXTURE)
    terms = [d.term_en for d in parsed.definitions]
    assert "reporting entity" in terms


def test_amendments_extracted_from_historical_notes():
    parsed = parse_document(FIXTURE)
    # The HistoricalNote under section 2 should produce at least one row.
    assert any("SOR/2099-001" in a.text for a in parsed.amendments)


def test_parse_document_accepts_raw_bytes():
    xml_bytes = FIXTURE.read_bytes()
    parsed = parse_document(xml_bytes, source_url="https://example.test/sample.xml")
    assert parsed.source_url == "https://example.test/sample.xml"
    assert parsed.document_type == "regulation"


# --------------------------------------------------------------------
# Real-world smoke test against the consolidated Bank Act XML. Numbers
# in the asserts are intentionally loose ranges rather than exact
# counts so the test stays green as the upstream consolidation drifts;
# the point is to catch parser regressions, not to pin the snapshot.
# --------------------------------------------------------------------

BANK_ACT = Path(__file__).parent / "fixtures" / "B-1.01.xml"


def test_bank_act_parses_statute_identification():
    parsed = parse_document(BANK_ACT)
    assert parsed.document_type == "statute"
    assert parsed.short_title == "Bank Act"
    assert parsed.long_title == "An Act respecting banks and banking"
    assert parsed.url_code == "B-1.01"
    # Citation pattern: "S.C. <year>, c. <chapter>" — the original act
    # passed in 1991 as c. 46.
    assert "1991" in parsed.formal_citation
    assert "c. 46" in parsed.formal_citation


def test_bank_act_extracts_expected_structures():
    parsed = parse_document(BANK_ACT)
    # The Bank Act is a large statute — sanity-check that all the
    # major dataclass lists are populated. Lower bounds are
    # conservative to absorb consolidation drift.
    assert len(parsed.sections) > 500
    assert len(parsed.provisions) > 5000
    assert len(parsed.definitions) > 100
    assert len(parsed.amendments) > 500
    assert len(parsed.headings) > 100
    assert len(parsed.schedules) >= 1
