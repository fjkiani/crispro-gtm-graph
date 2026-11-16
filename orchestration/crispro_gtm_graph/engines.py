"""CrisPRO engine execution: live HTTP first, in-process fallback, fail-closed.

Endpoints (per directive):
  POST /api/ayesha/therapy-fit/bundle   -> crispro-backend-v3 (in-process TestClient fallback)
  POST /v1/ranker/8d/bilinear           -> Stage-2A signed decoder (SHA-256 verified,
                                           identical W_MoA math to the signed engine)
  POST /api/sae/extract_features        -> crispro-backend-v2 (requires live Modal SAE
                                           service; unavailable here -> recorded honestly,
                                           verified SAE values come from frozen receipts)

NOTE on the bilinear ranker: the v2 repo exposes ``predict_bilinear_8d`` as a
Python method (api/ml/crispro_unified_ml.py) but ships no HTTP route at
``/v1/ranker/8d/bilinear``. The full ``CrisPROUnifiedMLEngine.load()`` import
chain is broken in-repo (missing api/services/trials/door_matched_lift.py),
so this module re-implements ONLY the Stage-2A path: SHA-256 verification of
``crispro_8d_bilinear_decoder_v1.json`` against the ARTIFACT_LOCKS digest,
then the exact ``W_MoA.T @ x`` scoring the signed engine performs.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel

from . import config

_LIVE_TIMEOUT = 3.0

# Demo patient vectors (RUO, documented): uniform prior by default; the
# merck_tuvusertib campaign emphasizes d1_ddr_rs (DDR/replication stress axis)
# consistent with its MBD4-loss ATR-inhibitor context.
DEMO_PATIENT_8D: Dict[str, List[float]] = {
    "default": [0.5] * 8,
    "merck_tuvusertib": [0.9, 0.3, 0.2, 0.2, 0.4, 0.3, 0.2, 0.2],
}


class EngineCallResult(BaseModel):
    endpoint: str
    mode: str  # live_http | in_process | unavailable
    ok: bool
    status_code: Optional[int] = None
    summary: Dict[str, Any]
    error: Optional[str] = None


# --------------------------------------------------------------------------
# In-process singletons (import once per process)
# --------------------------------------------------------------------------
_v3_client = None
_v3_lock = threading.Lock()


def _get_v3_test_client():
    """Lazily import crispro-backend-v3 main and wrap it in a TestClient."""
    global _v3_client
    with _v3_lock:
        if _v3_client is not None:
            return _v3_client
        v3_root = str(config.CRISPRO_V3_ROOT)
        if v3_root not in sys.path:
            sys.path.insert(0, v3_root)
        import main as v3_main  # noqa: WPS433

        from fastapi.testclient import TestClient

        _v3_client = TestClient(v3_main.app)
        return _v3_client


# --------------------------------------------------------------------------
# Stage-2A bilinear decoder (SHA-256 verified, in-process)
# --------------------------------------------------------------------------
_bilinear_cache: Optional[Dict[str, Any]] = None


def load_bilinear_decoder() -> Dict[str, Any]:
    """Load + SHA-verify the signed Stage-2A decoder artifact (cached)."""
    global _bilinear_cache
    if _bilinear_cache is not None:
        return _bilinear_cache
    import hashlib

    path = config.V2_MODELS_DIR / config.BILINEAR_DECODER_LOCK["path"]
    if not path.exists():
        raise FileNotFoundError(f"Stage-2A decoder artifact missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = config.BILINEAR_DECODER_LOCK["sha256"]
    if digest != expected:
        raise RuntimeError(
            f"Stage-2A SHA-256 mismatch: got {digest}, expected {expected} — refusing to run (fail-closed)"
        )
    _bilinear_cache = {
        "artifact": json.loads(path.read_text(encoding="utf-8")),
        "sha256": digest,
        "path": str(path),
    }
    return _bilinear_cache


def predict_bilinear_8d(patient_8d: List[float]) -> Dict[str, Any]:
    """Signed 8D -> 15 MoA scoring; identical math to CrisPROUnifiedMLEngine."""
    import numpy as np

    cached = load_bilinear_decoder()
    dec = cached["artifact"]
    x = np.asarray(list(patient_8d), dtype=float).reshape(8)
    W = np.asarray(dec["W_MoA_matrix"], dtype=float)
    if W.shape != (8, 15):
        raise ValueError(f"W_MoA shape {W.shape}, expected (8, 15)")
    scores = W.T @ x
    moas = list(dec["moa_classes_15"])
    ranked = sorted(
        [{"moa": m, "score": float(s)} for m, s in zip(moas, scores)],
        key=lambda r: -r["score"],
    )
    summary = dec.get("loto_validation_summary") or {}
    return {
        "ranked_moa": ranked,
        "scores_15d": [float(s) for s in scores],
        "mean_loto_auroc": summary.get("mean_loto_cohort_auroc"),
        "model_id": dec.get("model_id"),
        "ruo": True,
        "artifact_sha256": cached["sha256"],
    }


# --------------------------------------------------------------------------
# Endpoint executors
# --------------------------------------------------------------------------
def _try_live(method: str, url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        resp = httpx.request(method, url, json=payload, timeout=_LIVE_TIMEOUT)
        return {"status_code": resp.status_code, "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text}
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        return None
    except Exception as exc:  # malformed responses etc.
        return {"status_code": None, "body": None, "error": str(exc)}


def call_ayesha_bundle(target_id: str) -> EngineCallResult:
    payload: Dict[str, Any] = {}
    if target_id == "ayesha_clinical_brief":
        profile = config.V3_AYESHA_FIXTURES / "profile.json"
        if profile.exists():
            blob = json.loads(profile.read_text(encoding="utf-8"))
            bm = blob.get("biomarkers") or {}
            payload = {"hrd_status": bm.get("hrd_status")}
    live = _try_live("POST", f"{config.CRISPRO_API_V3_URL}/api/ayesha/therapy-fit/bundle", payload)
    if live is not None and live.get("status_code") == 200:
        return EngineCallResult(
            endpoint="POST /api/ayesha/therapy-fit/bundle",
            mode="live_http", ok=True, status_code=200,
            summary={"keys": list(live["body"].keys()) if isinstance(live["body"], dict) else []},
        )
    # in-process fallback (proven working in this environment)
    try:
        client = _get_v3_test_client()
        resp = client.post("/api/ayesha/therapy-fit/bundle", json=payload)
        body = resp.json()
        return EngineCallResult(
            endpoint="POST /api/ayesha/therapy-fit/bundle",
            mode="in_process", ok=resp.status_code == 200, status_code=resp.status_code,
            summary={
                "completeness_score": (body.get("completeness") or {}).get("completeness_score"),
                "level": (body.get("completeness") or {}).get("level_name"),
                "boardroom_outcome": ((body.get("resistance_gate") or {}).get("boardroom_outcome") or {}).get("label"),
                "top_level_keys": list(body.keys())[:12],
            },
        )
    except Exception as exc:
        return EngineCallResult(
            endpoint="POST /api/ayesha/therapy-fit/bundle",
            mode="unavailable", ok=False,
            summary={}, error=f"live HTTP and in-process both failed: {exc}",
        )


def call_bilinear_ranker(target_id: str, patient_8d: Optional[List[float]] = None) -> EngineCallResult:
    vector = patient_8d or DEMO_PATIENT_8D.get(target_id, DEMO_PATIENT_8D["default"])
    live = _try_live("POST", f"{config.CRISPRO_API_V2_URL}/v1/ranker/8d/bilinear", {"patient_8d": vector})
    if live is not None and live.get("status_code") == 200:
        return EngineCallResult(
            endpoint="POST /v1/ranker/8d/bilinear",
            mode="live_http", ok=True, status_code=200,
            summary={"source": "live v2 route"},
        )
    # in-process fallback: SHA-verified Stage-2A decoder
    try:
        result = predict_bilinear_8d(vector)
        top3 = [
            {"moa": r["moa"], "score": round(r["score"], 4)}
            for r in result["ranked_moa"][:3]
        ]
        return EngineCallResult(
            endpoint="POST /v1/ranker/8d/bilinear",
            mode="in_process", ok=True, status_code=200,
            summary={
                "note": "v2 ships no HTTP route for the 8d/bilinear ranker; executed via SHA-256-verified Stage-2A decoder in-process (identical W_MoA math)",
                "patient_8d": vector,
                "top3_moa": top3,
                "mean_loto_auroc": result["mean_loto_auroc"],
                "artifact_sha256": result["artifact_sha256"][:16] + "…",
                "ruo": True,
            },
        )
    except Exception as exc:
        return EngineCallResult(
            endpoint="POST /v1/ranker/8d/bilinear",
            mode="unavailable", ok=False,
            summary={}, error=f"fail-closed: {exc}",
        )


def call_sae_extract(target_id: str) -> EngineCallResult:
    payload = {
        "chrom": "17", "pos": 7673802, "ref": "C", "alt": "T",
        "model_id": "evo2_7b_base", "assembly": "GRCh38",
    }
    live = _try_live("POST", f"{config.CRISPRO_API_V2_URL}/api/sae/extract_features", payload)
    if live is not None and live.get("status_code") == 200:
        return EngineCallResult(
            endpoint="POST /api/sae/extract_features",
            mode="live_http", ok=True, status_code=200,
            summary={"source": "live v2 SAE route"},
        )
    return EngineCallResult(
        endpoint="POST /api/sae/extract_features",
        mode="unavailable", ok=False,
        summary={
            "reason": (
                "Requires the live Modal-hosted SAE service behind the v2 route "
                "(ENABLE_TRUE_SAE flag + https://fjkiani--sae-service-saeservice-api.us-east.modal.run). "
                "Not reachable from this environment — fail-closed, no SAE numbers invented. "
                "Verified SAE values for copy are loaded from the frozen "
                "diamond_sae_3level_validation_receipt.json / "
                "mbd4_next_pathway_sae_trajectory_receipt.json instead."
            )
        },
        error="SAE live service unreachable; no in-process fallback exists for the Modal SAE",
    )


def execute_all_engines(target_id: str, patient_8d: Optional[List[float]] = None) -> List[EngineCallResult]:
    return [
        call_ayesha_bundle(target_id),
        call_bilinear_ranker(target_id, patient_8d),
        call_sae_extract(target_id),
    ]
