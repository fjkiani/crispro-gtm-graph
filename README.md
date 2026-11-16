# CrisPRO-GTM-Graph

Deterministic, fail-closed **LangGraph StateGraph** orchestrator connecting the
CrisPRO ML backends, the AACR 2026 session extractions, automated Anti-Slop /
UI-UX jargon gates with retry loops, and the `crispro-gtm-studio` Palmier MCP
video builder. It removes the human operator as the message bus between ML
backends, manuscripts, slop audits, and GTM asset builders.

```
slop_scout -> execute_ml -> anti_slop_gate -> gtm_architect -> ux_jargon_gate
           -> build_assets -> [INTERRUPT: human approval] -> dispatch_package

anti_slop_gate FAIL -> execute_ml   (max 2 retries, else ESCALATED)
ux_jargon_gate FAIL -> gtm_architect (offending phrases carried; max 2 retries)
```

## Layout

```
orchestration/crispro_gtm_graph/
  config.py             env-overridable paths (see docstring)
  campaigns.py          target campaign registry (6 campaigns)
  aacr_2026_indexer.py  Strike 1: SQLite FTS5 index over schema_a + schema_b
  evidence.py           VerifiedEvidencePacket (fail-closed evidence loading)
  engines.py            live HTTP -> in-process TestClient fallback executors
  tools.py              Strike 2: the four typed tools
  copy.py               deterministic draft templates (numbers only from packet)
  graph.py              Strike 3: StateGraph + SqliteSaver + interrupt
  routers/kb_router.py  GET /api/kb/aacr2026/search        (drop-in)
  routers/gtm.py        POST /api/v1/gtm/campaigns/run ... (drop-in)
tests/test_crispro_gtm_graph.py   Strike 4: 30 tests incl. full E2E
scripts/build_index.py            build the FTS5 index
data/kb/aacr_2026_index.sqlite    built index (986 talks)
```

## Quickstart

```bash
pip install -r requirements.txt
python scripts/build_index.py          # build data/kb/aacr_2026_index.sqlite
pytest tests/ -q                       # 30 tests incl. E2E merck_tuvusertib
```

## Mounting into oncology-coPilot / oncology-backend-minimal

In `api/main.py`:

```python
from orchestration.crispro_gtm_graph.routers import kb_router, gtm
app.include_router(kb_router.router)   # GET  /api/kb/aacr2026/search
app.include_router(gtm.router)         # POST /api/v1/gtm/campaigns/run, /approve
```

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/gtm/campaigns/run` | run a campaign; pauses at the dispatch interrupt |
| POST | `/api/v1/gtm/campaigns/{thread_id}/approve` | resume past human approval |
| GET  | `/api/v1/gtm/campaigns/{thread_id}/state` | inspect checkpointed state |
| GET  | `/api/v1/gtm/campaigns` | list campaigns |
| GET  | `/api/kb/aacr2026/search?q=...` | live FTS5 search |
| GET  | `/api/kb/aacr2026/stats` | index stats |

## Environment

All paths are env-overridable (`AACR_SCHEMA_A_DIR`, `CRISPRO_V3_ROOT`,
`CRISPRO_V2_ROOT`, `CRISPRO_FRONTEND_ROOT`, `PUBLICATIONS_DIR`,
`CRISPRO_API_V3_URL`, `CRISPRO_API_V2_URL`, `PALMIER_MCP_URL`, ...) — see
`config.py`. On the macOS host (`crispr-assistant-main`), point them at the
local checkouts; `publications/` figure loading activates automatically when
that directory exists.

## Honest boundaries (fail-closed by design)

- **Zero AACR coverage is reported as zero.** camonsertib, tuvusertib,
  saruparib, daraxonrasib, STC-1010 have no AACR 2026 session-extraction hits;
  the scout says so instead of inventing authority.
- **SAE live inference** requires the Modal-hosted service behind v2's
  `/api/sae/extract_features` (`ENABLE_TRUE_SAE`). When unreachable, the
  engine result is recorded `unavailable` and copy uses only values from the
  frozen SHA-256-locked receipts.
- **The 8D bilinear ranker** has no shipped HTTP route in v2; the graph
  executes the identical Stage-2A math in-process after re-verifying the
  decoder artifact against its ARTIFACT_LOCKS SHA-256.
- **Repo Slop Patrol** found 31 pre-existing HTTP 501 stubs in v2 legacy
  routers (evidence/agents/dsr/myeloma/resistance) — outside the GTM-critical
  path, surfaced as a FINDING, never hidden. The GTM-critical stack (v3 +
  this package) has zero.
- **Every number in generated copy** must match the VerifiedEvidencePacket
  (frozen receipts, signed artifacts, live engine outputs, or user-supplied
  registry identifiers, each labelled by provenance). Unverified numbers,
  mirage drugs (Vismodegib always; Olaparib in MBD4-only contexts), banned
  phrases, and headline ML jargon fail the gates automatically.
- **Palmier integration is wired into the `crispro-gtm-studio` kit, not
  reimplemented.** `palmier_bridge.py` imports the kit's own audited client
  (`scripts/mcp_client.py` — `PalmierMCP`, session-aware, 404 re-init, SSE
  decoding), mirrors the kit's timeline grammar (`timelines/video2_pharma_sniper.py`:
  24 fps frame math, 6-beat structure at frames 0/72/192/264/408/504,
  `tools/call` JSON-RPC, one-target-one-render), and enforces the SKILL.md
  verification gate: trial claims come only from the curated target card
  (`targets/{target}.json`); a missing card blocks live render and writes a
  draft skeleton (registry identifiers only, nulls elsewhere) instead of
  improvising a narrative. Campaign→kit mapping: repare/merck/astrazeneca/
  brenus/sanofi → `pharma-bd-sniper-reel` (video2), ayesha →
  `clinical-case-60s-brief` (video3). A stub-MCP integration test proves the
  kit client executes the queue live end-to-end.
- **RUO** labelling is enforced on all outputs.
