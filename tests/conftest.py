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


import shutil

import pytest


@pytest.fixture()
def tmp_kit(tmp_path, monkeypatch):
    """Hermetic crispro-gtm-studio kit copy: real mcp_client.py, empty targets/."""
    from orchestration.crispro_gtm_graph import config

    real_kit = config.CRISPRO_GTM_STUDIO_DIR
    src = real_kit / "scripts" / "mcp_client.py"
    if not src.exists():
        pytest.skip(f"crispro-gtm-studio kit not present at {real_kit}")
    kit = tmp_path / "crispro-studio"
    (kit / "scripts").mkdir(parents=True)
    (kit / "targets").mkdir()
    (kit / "cases").mkdir()
    shutil.copy(src, kit / "scripts" / "mcp_client.py")
    monkeypatch.setattr(config, "CRISPRO_GTM_STUDIO_DIR", kit)
    return kit

