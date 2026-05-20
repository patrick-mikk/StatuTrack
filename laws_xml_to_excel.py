#!/usr/bin/env python3
"""Backwards-compatible CLI shim.

The original 2000-line module was moved to ``statutrack/parser/legacy.py``
during the Phase 1 refactor so the parsing core is importable as a
library. Existing users who still invoke ``python laws_xml_to_excel.py``
keep working unchanged.
"""
from statutrack.parser.legacy import main


if __name__ == "__main__":
    raise SystemExit(main())
