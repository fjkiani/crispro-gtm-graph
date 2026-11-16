"""Path configuration for the CrisPRO-GTM-Graph orchestrator.

All paths are environment-overridable so the package is a drop-in for
``crispr-assistant-main`` (macOS) and for CI/sandbox runs (Linux).

Env vars
--------
AACR_SCHEMA_A_DIR        schema_a (scientific) extraction JSONs
AACR_SCHEMA_B_DIR        schema_b (contrarian/vulnerability) extraction JSONs
AACR_INDEX_PATH          SQLite FTS5 index location
CRISPRO_V3_ROOT          crispro-backend-v3 checkout (bundle endpoint, trials DB)
CRISPRO_V2_ROOT          crispro-backend-v2 checkout (bilinear decoder, SAE router, receipts)
CRISPRO_FRONTEND_ROOT    crispro-frontend-v2 checkout (Repo Slop Patrol)
CRISPRO_GTM_STUDIO_DIR   crispro-gtm-studio kit (mcp_client.py)
PUBLICATIONS_DIR         publications/{00-mbd4,04-mm,12-brm,15-ovarian,16-immunotherapy}
GTM_GRAPH_DATA_DIR       graph runtime data (checkpoints, campaign outputs)
CRISPRO_API_V3_URL       live v3 backend base URL (default http://127.0.0.1:8000)
CRISPRO_API_V2_URL       live v2 backend base URL (default http://127.0.0.1:8001)
PALMIER_MCP_URL          Palmier Pro MCP endpoint (default http://127.0.0.1:19789/mcp)
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root = .../crispro-gtm-graph  (this file is 3 levels deep)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: str | Path) -> Path:
    return Path(os.environ.get(name, str(default)))


def _workspace_default(*parts: str) -> Path:
    """Sandbox default under /workspace; falls back to REPO_ROOT-relative on other hosts."""
    cand = Path("/workspace") / "/".join(parts)
    return cand if cand.exists() else REPO_ROOT / "/".join(parts[1:])


AACR_SCHEMA_A_DIR = _env_path("AACR_SCHEMA_A_DIR", _workspace_default("aacr-2026", "extractions", "schema_a"))
AACR_SCHEMA_B_DIR = _env_path("AACR_SCHEMA_B_DIR", _workspace_default("aacr-2026", "extractions", "schema_b"))

GTM_GRAPH_DATA_DIR = _env_path("GTM_GRAPH_DATA_DIR", REPO_ROOT / "data")
AACR_INDEX_PATH = _env_path("AACR_INDEX_PATH", GTM_GRAPH_DATA_DIR / "kb" / "aacr_2026_index.sqlite")
CHECKPOINT_PATH = _env_path("GTM_CHECKPOINT_PATH", GTM_GRAPH_DATA_DIR / "gtm_graph_checkpoints.sqlite")
CAMPAIGN_OUTPUT_DIR = _env_path("GTM_CAMPAIGN_OUTPUT_DIR", GTM_GRAPH_DATA_DIR / "gtm_campaigns")

CRISPRO_V3_ROOT = _env_path("CRISPRO_V3_ROOT", _workspace_default("crispro-backend-v3"))
CRISPRO_V2_ROOT = _env_path("CRISPRO_V2_ROOT", _workspace_default("crispro-backend-v2"))
CRISPRO_FRONTEND_ROOT = _env_path("CRISPRO_FRONTEND_ROOT", _workspace_default("crispro-frontend-v2"))
CRISPRO_GTM_STUDIO_DIR = _env_path("CRISPRO_GTM_STUDIO_DIR", _workspace_default("crispro-gtm-studio", "crispro-studio"))
PUBLICATIONS_DIR = _env_path("PUBLICATIONS_DIR", REPO_ROOT / "publications")

CRISPRO_API_V3_URL = os.environ.get("CRISPRO_API_V3_URL", "http://127.0.0.1:8000")
CRISPRO_API_V2_URL = os.environ.get("CRISPRO_API_V2_URL", "http://127.0.0.1:8001")
PALMIER_MCP_URL = os.environ.get("PALMIER_MCP_URL", "http://127.0.0.1:19789/mcp")

# v2 frozen ML artifacts (SHA-256 locked in api/ml/crispro_unified_ml.py ARTIFACT_LOCKS)
V2_MODELS_DIR = CRISPRO_V2_ROOT / "api" / "resources" / "models"
V2_TRIALS_DB = CRISPRO_V3_ROOT / "data" / "clinical_trials.db"
V3_AYESHA_FIXTURES = CRISPRO_V3_ROOT / "fixtures" / "ayesha"

# Stage-2A lock (copied verbatim from ARTIFACT_LOCKS in crispro-backend-v2;
# the loader re-verifies the artifact against this expected digest at runtime).
BILINEAR_DECODER_LOCK = {
    "path": "crispro_8d_bilinear_decoder_v1.json",
    "sha256": "fabe9feabd28e9fe9174bad8e44ac7203a5647ca0faed589f21f7b26de7ee026",
    "metric": "mean_loto_cohort_auroc=0.8078",
}
