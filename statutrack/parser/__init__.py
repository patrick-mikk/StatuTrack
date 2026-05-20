"""Placeholder for Phase 1 refactor.

The current canonical implementation lives in ``laws_xml_to_excel.py`` at
the repo root. Phase 1 will extract the XML -> ``ParsedDocument`` core
into this package, leaving Excel rendering as one consumer
(``statutrack.parser.excel``) and the SQLite ingest as another
(``statutrack.ingest.loader``).
"""
