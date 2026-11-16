"""Drop-in KB router: GET /api/kb/aacr2026/search over the FTS5 index.

Mount in oncology-coPilot/oncology-backend-minimal/api/main.py:

    from orchestration.crispro_gtm_graph.routers import kb_router
    app.include_router(kb_router.router)

The index location is resolved via AACR_INDEX_PATH (see config.py).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import aacr_2026_indexer

router = APIRouter(prefix="/api/kb/aacr2026", tags=["kb", "aacr-2026"])


@router.get("/search")
def kb_aacr2026_search(
    q: str = Query(..., min_length=1, description="Drug, target, speaker, institution, NCT id, or pitfall keyword"),
    category: Optional[str] = Query(
        None,
        pattern="^(speaker|institution|nct|drug|pitfall)$",
        description="Optional category filter",
    ),
    limit: int = Query(20, ge=1, le=100),
):
    """Live FTS5 search over the AACR 2026 session index.

    Zero-hit responses are honest: drugs with no AACR 2026 session coverage
    return total_hits=0 (never invented).
    """
    try:
        return aacr_2026_indexer.search(q, category=category, limit=limit)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stats")
def kb_aacr2026_stats():
    """Index build stats (files, sessions, talks, speakers, NCT ids)."""
    try:
        return aacr_2026_indexer.index_stats()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
