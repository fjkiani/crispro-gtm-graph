"""Strike 1: SQLite FTS5 index over the AACR 2026 session extractions.

Indexes BOTH extraction schemas and joins them by ``talk_id``:

* schema_a (scientific): targets, biomarkers, models, clinical_stage,
  tumor_types, topic_categories, MOA_summary
* schema_b (contrarian): vulnerability_identified, trial_dilution_risk,
  competitive_moat_weakness, cognitive_dissonance, crispro_opportunity,
  cited_competitors, unresolved_questions, external_follow_up (NCT numbers)

The index is fail-closed about coverage: drugs with zero AACR 2026 sessions
return zero hits (never invented).

CLI:  python scripts/build_index.py
API:  from orchestration.crispro_gtm_graph.aacr_2026_indexer import build_index, search
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_slug TEXT PRIMARY KEY,
    session_title TEXT,
    file_a TEXT,
    file_b TEXT
);
CREATE TABLE IF NOT EXISTS talks (
    talk_id TEXT PRIMARY KEY,
    session_slug TEXT,
    talk_title TEXT,
    speaker_name TEXT,
    affiliation TEXT,
    tumor_types TEXT,
    clinical_stage TEXT,
    topic_categories TEXT,
    targets TEXT,
    biomarkers TEXT,
    moa_summary TEXT,
    vulnerability_text TEXT,
    crispro_opportunity_text TEXT,
    nct_ids TEXT,
    competitors TEXT,
    rhetorical_signals TEXT,
    payload_a TEXT,
    payload_b TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS talks_fts USING fts5(
    talk_id UNINDEXED,
    body
);
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_NCT_RE = re.compile(r"\bNCT\d{2,12}\b", re.IGNORECASE)


def _flatten(obj: Any, out: List[str]) -> None:
    """Recursively flatten every string leaf of a JSON structure."""
    if obj is None:
        return
    if isinstance(obj, str):
        if obj.strip():
            out.append(obj.strip())
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _flatten(item, out)
    elif isinstance(obj, dict):
        for key, val in obj.items():
            _flatten(val, out)
            if isinstance(key, str) and key not in ("transcript_evidence",):
                pass  # keys are not indexed; values carry the content


def _flatten_text(obj: Any) -> str:
    parts: List[str] = []
    _flatten(obj, parts)
    return " | ".join(dict.fromkeys(parts))  # de-dup, keep order


def _session_slug_from_talk_id(talk_id: str) -> str:
    return talk_id.split("::")[0] if "::" in talk_id else talk_id


def _join_key(talk_id: str) -> tuple:
    """(session_slug, ordinal) join key.

    schema_a talk_ids use the speaker surname (``session::gill::1``) while
    schema_b uses ``session::unknown::1`` when the speaker is not identified,
    so raw talk_id joins fail. The stable join key is (session slug, ordinal).
    """
    parts = talk_id.split("::")
    if len(parts) >= 3:
        return (parts[0], parts[-1])
    if len(parts) == 2:
        return (parts[0], parts[1])
    return (talk_id, "")


def build_index(
    schema_a_dir: Path = config.AACR_SCHEMA_A_DIR,
    schema_b_dir: Path = config.AACR_SCHEMA_B_DIR,
    db_path: Path = config.AACR_INDEX_PATH,
) -> Dict[str, Any]:
    """Build (or rebuild) the FTS5 index. Returns honest stats."""
    if not schema_a_dir.exists():
        raise FileNotFoundError(f"schema_a dir not found: {schema_a_dir}")
    if not schema_b_dir.exists():
        raise FileNotFoundError(f"schema_b dir not found: {schema_b_dir}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)

    a_files = sorted(schema_a_dir.glob("*.json"))
    b_files = sorted(schema_b_dir.glob("*.json"))

    talks_a: Dict[tuple, Any] = {}
    talk_ids_a: Dict[tuple, str] = {}
    session_titles: Dict[str, str] = {}
    for fp in a_files:
        try:
            rows = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Unparseable schema_a file {fp.name}: {exc}") from exc
        if not isinstance(rows, list):
            continue
        for row in rows:
            tid = row.get("talk_id")
            if not tid:
                continue
            key = _join_key(tid)
            talks_a.setdefault(key, row)
            talk_ids_a.setdefault(key, tid)
            slug = _session_slug_from_talk_id(tid)
            session_titles.setdefault(slug, row.get("session_title") or fp.stem)
            conn.execute(
                "INSERT OR IGNORE INTO sessions(session_slug, session_title, file_a, file_b) VALUES (?,?,?,NULL)",
                (slug, session_titles[slug], fp.name),
            )

    talks_b: Dict[tuple, Any] = {}
    talk_ids_b: Dict[tuple, str] = {}
    for fp in b_files:
        try:
            rows = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Unparseable schema_b file {fp.name}: {exc}") from exc
        if not isinstance(rows, list):
            continue
        for row in rows:
            tid = row.get("talk_id")
            if not tid:
                continue
            key = _join_key(tid)
            talks_b.setdefault(key, row)
            talk_ids_b.setdefault(key, tid)
            slug = _session_slug_from_talk_id(tid)
            conn.execute(
                "INSERT OR IGNORE INTO sessions(session_slug, session_title, file_a, file_b) VALUES (?,?,NULL,?)",
                (slug, session_titles.get(slug) or fp.stem, fp.name),
            )

    n_talks = 0
    n_joined = 0
    speakers: Dict[str, List[str]] = {}
    all_nct: set = set()

    def _index_talk(key: tuple, a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> None:
        nonlocal n_talks, n_joined
        tid = (talk_ids_a if a is not None else talk_ids_b).get(key) or "::".join(key)
        a = a or {}
        b = b or {}
        meta = b.get("talk_metadata", {}) or {}
        speaker = (a.get("speaker") or {})
        speaker_name = speaker.get("name") or meta.get("speaker_name") or ""
        affiliation = speaker.get("affiliation") or meta.get("institution_or_pharma") or ""

        vuln_text = _flatten_text(
            {k: b.get(k) for k in (
                "vulnerability_identified", "trial_dilution_risk",
                "competitive_moat_weakness", "cognitive_dissonance",
                "unresolved_questions",
            ) if b.get(k)}
        )
        opp_text = _flatten_text(b.get("crispro_opportunity"))
        nct_ids = sorted(set(
            _NCT_RE.findall(json.dumps(b)) + _NCT_RE.findall(json.dumps(a))
        ))
        all_nct.update(nct_ids)
        if speaker_name:
            sp_key = f"{speaker_name} ({affiliation})" if affiliation else speaker_name
            speakers.setdefault(sp_key, []).append(tid)

        body = " | ".join(filter(None, [
            tid, a.get("talk_title") or "", meta.get("talk_title") or "",
            a.get("session_title") or "", meta.get("session_title") or "",
            speaker_name, affiliation,
            ", ".join(a.get("tumor_types") or []),
            a.get("clinical_stage") or "",
            ", ".join(a.get("topic_categories") or []),
            _flatten_text(a.get("targets")),
            _flatten_text(a.get("biomarkers")),
            a.get("MOA_summary") or "",
            vuln_text, opp_text,
            _flatten_text(b.get("cited_competitors")),
            _flatten_text(meta.get("rhetorical_signals")),
            " ".join(nct_ids),
        ]))

        conn.execute(
            "INSERT OR REPLACE INTO talks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid, _session_slug_from_talk_id(tid),
                a.get("talk_title") or meta.get("talk_title") or "",
                speaker_name, affiliation,
                ", ".join(a.get("tumor_types") or []),
                a.get("clinical_stage") or "",
                ", ".join(a.get("topic_categories") or []),
                _flatten_text(a.get("targets")),
                _flatten_text(a.get("biomarkers")),
                a.get("MOA_summary") or "",
                vuln_text, opp_text,
                " ".join(nct_ids),
                _flatten_text(b.get("cited_competitors")),
                _flatten_text(meta.get("rhetorical_signals")),
                json.dumps(a) if a else "NULL",
                json.dumps(b) if b else "NULL",
            ),
        )
        conn.execute("INSERT INTO talks_fts(talk_id, body) VALUES (?,?)", (tid, body))
        n_talks += 1
        if a and b:
            n_joined += 1

    all_keys = sorted(set(talks_a) | set(talks_b))
    for key in all_keys:
        _index_talk(key, talks_a.get(key), talks_b.get(key))

    stats = {
        "schema_a_files": len(a_files),
        "schema_b_files": len(b_files),
        "sessions": conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
        "talks_indexed": n_talks,
        "talks_joined_a_and_b": n_joined,
        "distinct_speakers": len(speakers),
        "distinct_nct_ids": len(all_nct),
        "db_path": str(db_path),
    }
    for k, v in stats.items():
        conn.execute("INSERT OR REPLACE INTO index_meta VALUES (?,?)", (k, str(v)))
    conn.commit()
    conn.close()
    return stats


def _sanitize_token(tok: str) -> List[str]:
    """Split a raw token into FTS-safe alphanumeric sub-tokens.

    Hyphens/dots are token separators in FTS5 unicode61, so 'stc-1010' must
    become the conjunction (stc* AND 1010*), not the invalid expression
    'stc-1010*'.
    """
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", tok) if p]
    return parts


def _match_expr(query: str) -> str:
    """Build an FTS5 MATCH expression: OR of prefix terms; quoted phrases kept."""
    phrases = re.findall(r'"([^"]+)"', query)
    remainder = re.sub(r'"[^"]+"', " ", query)
    terms: List[str] = []
    for ph in phrases:
        sub: List[str] = []
        for tok in ph.split():
            sub.extend(t + "*" for t in _sanitize_token(tok))
        if sub:
            terms.append("(" + " AND ".join(sub) + ")")
    for tok in remainder.split():
        sub = [t + "*" for t in _sanitize_token(tok)]
        if sub:
            terms.append("(" + " AND ".join(sub) + ")" if len(sub) > 1 else sub[0])
    if not terms:
        raise ValueError(f"Query has no indexable tokens: {query!r}")
    return " OR ".join(terms)


def search(
    query: str,
    category: Optional[str] = None,
    limit: int = 20,
    db_path: Path = config.AACR_INDEX_PATH,
) -> Dict[str, Any]:
    """Search the AACR 2026 index.

    category: None (full text) | 'speaker' | 'institution' | 'nct' | 'drug' | 'pitfall'
    Returns honest zero-hit results when a drug has no AACR 2026 coverage.
    """
    if not db_path.exists():
        raise FileNotFoundError(
            f"AACR index not built at {db_path}. Run scripts/build_index.py first."
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        match = _match_expr(query)
        sql = (
            "SELECT t.talk_id, t.talk_title, t.speaker_name, t.affiliation, "
            "t.session_slug, t.tumor_types, t.clinical_stage, t.targets, "
            "snippet(talks_fts, 1, '[', ']', '…', 24) AS snip "
            "FROM talks_fts JOIN talks t ON t.talk_id = talks_fts.talk_id "
            "WHERE talks_fts.body MATCH ?"
        )
        params: List[Any] = [match]
        q = query.lower().strip('"')
        if category == "speaker":
            sql += " AND lower(t.speaker_name) LIKE ?"
            params.append(f"%{q}%")
        elif category == "institution":
            sql += " AND lower(t.affiliation) LIKE ?"
            params.append(f"%{q}%")
        elif category == "nct":
            sql += " AND upper(t.nct_ids) LIKE ?"
            params.append(f"%{q.upper()}%")
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM talks_fts WHERE talks_fts.body MATCH ?", (match,)
        ).fetchone()[0]
        return {
            "query": query,
            "category": category,
            "total_hits": total,
            "returned": len(rows),
            "hits": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def index_stats(db_path: Path = config.AACR_INDEX_PATH) -> Dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(f"AACR index not built at {db_path}")
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM index_meta")
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(build_index(), indent=2))
