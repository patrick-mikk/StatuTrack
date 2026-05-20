-- StatuTrack SQLite schema (Phase 2 draft, not yet applied).
--
-- An "instrument" is an Act or Regulation (one row per language).
-- A "version" is one parsed snapshot of an instrument at a specific
-- commit in laws-lois-xml. A "section" is one row per leaf provision
-- inside a version (subsection / paragraph / subparagraph are flattened
-- with a citation). A "diff" is precomputed between two consecutive
-- versions of a section.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS instruments (
    id              INTEGER PRIMARY KEY,
    slug            TEXT NOT NULL,                 -- "SOR-2002-184"
    language        TEXT NOT NULL CHECK (language IN ('eng', 'fra')),
    type            TEXT NOT NULL CHECK (type IN ('statute', 'regulation')),
    short_title     TEXT NOT NULL,
    long_title      TEXT,
    citation        TEXT,                          -- "SOR/2002-184"
    enabling_act    TEXT,                          -- for regulations
    UNIQUE(slug, language)
);

CREATE INDEX IF NOT EXISTS idx_instruments_short_title ON instruments(short_title);

CREATE TABLE IF NOT EXISTS versions (
    id              INTEGER PRIMARY KEY,
    instrument_id   INTEGER NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    commit_hash     TEXT NOT NULL,
    commit_date     TEXT NOT NULL,                 -- ISO 8601, from git commit timestamp
    blob_sha        TEXT NOT NULL,                 -- git blob SHA of the XML at this commit;
                                                   -- two versions sharing a blob_sha are
                                                   -- byte-identical, used by the loader to
                                                   -- skip metadata-only churn.
    pit_date        TEXT,                          -- lims:pit-date from XML
    last_amended    TEXT,                          -- lims:lastAmendedDate
    parsed_at       TEXT NOT NULL,                 -- ingestion timestamp
    xml_path        TEXT NOT NULL,                 -- path inside laws-lois-xml
    UNIQUE(instrument_id, commit_hash)
);

CREATE INDEX IF NOT EXISTS idx_versions_instrument ON versions(instrument_id, commit_date DESC);

CREATE TABLE IF NOT EXISTS sections (
    id              INTEGER PRIMARY KEY,
    version_id      INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    citation        TEXT NOT NULL,                 -- "s. 71(1)(a)"
    section_number  TEXT NOT NULL,                 -- "71" (numeric, for ordering)
    subsection      TEXT,
    paragraph       TEXT,
    subparagraph    TEXT,
    clause          TEXT,
    marginal_note   TEXT,
    heading_path    TEXT,                          -- "Part 4 > Reporting > Records"
    content         TEXT NOT NULL,
    in_force_date   TEXT,
    fid             TEXT,                          -- LIMS fid for cross-version alignment
    ord             INTEGER NOT NULL               -- order within the version
);

CREATE INDEX IF NOT EXISTS idx_sections_version ON sections(version_id, ord);
CREATE INDEX IF NOT EXISTS idx_sections_fid ON sections(version_id, fid);
CREATE INDEX IF NOT EXISTS idx_sections_citation ON sections(version_id, citation);

CREATE TABLE IF NOT EXISTS diffs (
    id              INTEGER PRIMARY KEY,
    from_version_id INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    to_version_id   INTEGER NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
    citation        TEXT NOT NULL,
    change_type     TEXT NOT NULL CHECK (change_type IN ('added', 'removed', 'modified', 'renumbered', 'unchanged')),
    old_section_id  INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    new_section_id  INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    -- Pre-rendered HTML for inline diffs (<ins>/<del> spans). Generated
    -- once at ingest time so the serving layer never computes diffs.
    inline_html     TEXT,
    UNIQUE(from_version_id, to_version_id, citation)
);

CREATE INDEX IF NOT EXISTS idx_diffs_pair ON diffs(from_version_id, to_version_id);

-- Full-text search across section content. Populated by triggers below.
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    content,
    citation UNINDEXED,
    section_id UNINDEXED,
    version_id UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS sections_ai AFTER INSERT ON sections BEGIN
    INSERT INTO sections_fts(rowid, content, citation, section_id, version_id)
    VALUES (new.id, new.content, new.citation, new.id, new.version_id);
END;

CREATE TRIGGER IF NOT EXISTS sections_ad AFTER DELETE ON sections BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, content, citation, section_id, version_id)
    VALUES ('delete', old.id, old.content, old.citation, old.id, old.version_id);
END;

CREATE TRIGGER IF NOT EXISTS sections_au AFTER UPDATE ON sections BEGIN
    INSERT INTO sections_fts(sections_fts, rowid, content, citation, section_id, version_id)
    VALUES ('delete', old.id, old.content, old.citation, old.id, old.version_id);
    INSERT INTO sections_fts(rowid, content, citation, section_id, version_id)
    VALUES (new.id, new.content, new.citation, new.id, new.version_id);
END;
