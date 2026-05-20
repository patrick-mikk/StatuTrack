"""StatuTrack parser — XML -> in-memory ``ParsedDocument``.

The XML parsing logic lives in :mod:`statutrack.parser.legacy`, which is
the file refactored out of the original ``laws_xml_to_excel.py`` CLI
script. The split is deliberately conservative: the parsing internals
are untouched, and this module simply re-exports the stable public
surface so Phase 2 ingest, the Flask web layer, and tests can import
from one place without depending on legacy module names.

The Excel renderer (:func:`render_workbook`) is also re-exported because
it is one of two intended consumers of ``ParsedDocument`` — the other
being the SQLite ingest pipeline that arrives in Phase 2.
"""
from __future__ import annotations

from .legacy import (
    AmendmentRow,
    DefinitionRow,
    EnablingAuthorityRow,
    HeadingRow,
    ParsedAct,  # alias of ParsedDocument; kept for back-compat
    ParsedDocument,
    ProvisionRow,
    Schedule,
    ScheduleItem,
    load_xml,
    parse_document,
    render_workbook,
)

__all__ = [
    "AmendmentRow",
    "DefinitionRow",
    "EnablingAuthorityRow",
    "HeadingRow",
    "ParsedAct",
    "ParsedDocument",
    "ProvisionRow",
    "Schedule",
    "ScheduleItem",
    "load_xml",
    "parse_document",
    "render_workbook",
]
