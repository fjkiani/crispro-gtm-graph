#!/usr/bin/env python
"""Build the AACR 2026 FTS5 index (Strike 1).

Usage:
    python scripts/build_index.py            # default paths from config.py
    AACR_SCHEMA_A_DIR=... AACR_SCHEMA_B_DIR=... python scripts/build_index.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestration.crispro_gtm_graph.aacr_2026_indexer import build_index  # noqa: E402

if __name__ == "__main__":
    print(json.dumps(build_index(), indent=2))
