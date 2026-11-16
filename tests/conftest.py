import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_sessionstart(session):
    """Ensure the default FTS5 index exists (kb_router tests hit the default path)."""
    from orchestration.crispro_gtm_graph import aacr_2026_indexer, config

    if not config.AACR_INDEX_PATH.exists():
        if config.AACR_SCHEMA_A_DIR.exists() and config.AACR_SCHEMA_B_DIR.exists():
            aacr_2026_indexer.build_index()

