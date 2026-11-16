"""Drop-in GTM campaign router for oncology-coPilot/oncology-backend-minimal.

Mount in api/main.py:

    from orchestration.crispro_gtm_graph.routers import gtm
    app.include_router(gtm.router)

Endpoints
---------
POST /api/v1/gtm/campaigns/run                start a campaign (pauses at the
                                             human-approval interrupt before
                                             dispatch_package)
POST /api/v1/gtm/campaigns/{thread_id}/approve   resume past the interrupt
GET  /api/v1/gtm/campaigns/{thread_id}/state     inspect checkpointed state
GET  /api/v1/gtm/campaigns                     list known campaigns
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config
from ..campaigns import CAMPAIGNS
from ..graph import build_gtm_graph

router = APIRouter(prefix="/api/v1/gtm", tags=["gtm", "crispro-gtm-graph"])

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_gtm_graph()
    return _graph


class CampaignRunRequest(BaseModel):
    target_id: str = Field(..., description="One of: " + ", ".join(sorted(CAMPAIGNS)))
    patient_8d: Optional[List[float]] = Field(None, min_length=8, max_length=8)
    thread_id: Optional[str] = None


@router.get("/campaigns")
def list_campaigns():
    return {"campaigns": sorted(CAMPAIGNS.keys())}


@router.post("/campaigns/run")
def run_campaign(req: CampaignRunRequest):
    if req.target_id not in CAMPAIGNS:
        raise HTTPException(status_code=404, detail=f"Unknown target_id '{req.target_id}'")
    thread_id = req.thread_id or str(uuid.uuid4())
    graph = _get_graph()
    cfg = {"configurable": {"thread_id": thread_id}}
    initial_state = {"target_id": req.target_id, "retry_count": 0, "ux_retry_count": 0,
                     "approval_status": "AWAITING_APPROVAL"}
    if req.patient_8d:
        initial_state["patient_8d"] = req.patient_8d
    try:
        graph.invoke(initial_state, cfg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"campaign run failed: {exc}") from exc
    snapshot = graph.get_state(cfg)
    values = snapshot.values
    paused = snapshot.next and "dispatch_package" in snapshot.next
    return {
        "thread_id": thread_id,
        "target_id": req.target_id,
        "paused_at_interrupt": bool(paused),
        "next_node": list(snapshot.next) if snapshot.next else None,
        "approval_status": values.get("approval_status"),
        "anti_slop_verdict": values.get("anti_slop_verdict", {}).get("status"),
        "ux_lint_verdict": values.get("ux_lint_verdict", {}).get("status"),
        "built_artifacts": values.get("built_artifacts"),
        "state": values,
    }


@router.post("/campaigns/{thread_id}/approve")
def approve_campaign(thread_id: str):
    graph = _get_graph()
    cfg = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(cfg)
    if not snapshot or not snapshot.next:
        raise HTTPException(status_code=404, detail=f"No pending campaign for thread {thread_id}")
    if "dispatch_package" not in snapshot.next:
        raise HTTPException(status_code=409, detail=f"Thread {thread_id} is not paused at the dispatch gate")
    graph.invoke(None, cfg)
    final = graph.get_state(cfg)
    return {
        "thread_id": thread_id,
        "approval_status": final.values.get("approval_status"),
        "dispatched": final.values.get("approval_status") == "DISPATCHED",
        "built_artifacts": final.values.get("built_artifacts"),
    }


@router.get("/campaigns/{thread_id}/state")
def campaign_state(thread_id: str):
    graph = _get_graph()
    cfg = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(cfg)
    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail=f"Unknown thread {thread_id}")
    return {
        "thread_id": thread_id,
        "next_node": list(snapshot.next) if snapshot.next else None,
        "values": snapshot.values,
    }
