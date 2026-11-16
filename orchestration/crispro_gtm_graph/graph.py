"""Strike 3: the CrisPRO-GTM-Graph LangGraph StateGraph.

Topology
--------
slop_scout -> execute_ml -> anti_slop_gate -> gtm_architect -> ux_jargon_gate
           -> build_assets -> [INTERRUPT: human approval] -> dispatch_package

Retry edges (fail-closed):
  anti_slop_gate FAIL -> execute_ml   (max 2 retries, then ESCALATED)
  ux_jargon_gate FAIL -> gtm_architect (offending phrases carried; max 2
                                        retries, then ESCALATED)

Persistence: SqliteSaver at data/gtm_graph_checkpoints.sqlite with
interrupt_before=["dispatch_package"] — every campaign pauses for Alpha's
approval before external dispatch.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

from . import config
from .campaigns import get_campaign
from .copy import build_draft_assets
from .evidence import VerifiedEvidencePacket
from .tools import (
    build_gtm_assets_tool,
    deterministic_anti_slop_gate,
    execute_crispro_engines_tool,
    make_evidence_brief,
    scout_target_and_slop_tool,
)

MAX_RETRIES = 2


class GTMGraphState(TypedDict, total=False):
    target_id: str
    slop_audit: Dict[str, Any]
    evidence_packet: Dict[str, Any]
    engine_results: List[Dict[str, Any]]
    evidence_brief: str
    gtm_plan: Dict[str, Any]
    draft_assets: Dict[str, str]
    anti_slop_verdict: Dict[str, Any]
    ux_lint_verdict: Dict[str, Any]
    retry_count: int
    ux_retry_count: int
    offending_phrases: List[str]
    built_artifacts: Dict[str, Any]
    approval_status: str
    error: Optional[str]


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def slop_scout(state: GTMGraphState) -> Dict[str, Any]:
    audit = scout_target_and_slop_tool(state["target_id"])
    return {"slop_audit": audit}


def execute_ml(state: GTMGraphState) -> Dict[str, Any]:
    result = execute_crispro_engines_tool(state["target_id"])
    brief = make_evidence_brief(result)
    return {
        "evidence_packet": result["evidence_packet"],
        "engine_results": result["engine_results"],
        "evidence_brief": brief,
    }


def anti_slop_gate(state: GTMGraphState) -> Dict[str, Any]:
    verdict = deterministic_anti_slop_gate(state["evidence_packet"], state["evidence_brief"])
    update: Dict[str, Any] = {"anti_slop_verdict": verdict}
    if verdict["status"] == "FAIL":
        update["retry_count"] = state.get("retry_count", 0) + 1
    return update


def _strip_violations(drafts: Dict[str, str], offending: List[Dict[str, Any]]) -> Dict[str, str]:
    """Deterministically strip offending phrases / headline jargon from drafts."""
    cleaned = dict(drafts)
    for v in offending:
        phrase = v.get("phrase") or v.get("raw") or ""
        if not phrase:
            continue
        for name, text in cleaned.items():
            if v.get("kind") == "ml_jargon_in_headline":
                # remove the jargon token from headline lines only
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith(("#", "Subject:")) or i == 0:
                        lines[i] = re.sub(re.escape(phrase), "", line, flags=re.IGNORECASE)
                cleaned[name] = "\n".join(lines)
            else:
                cleaned[name] = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return cleaned


def gtm_architect(state: GTMGraphState) -> Dict[str, Any]:
    """Deterministic GTM plan + draft copy. Regenerates from the packet on
    every entry, so retry passes drop any previously flagged content."""
    target_id = state["target_id"]
    packet = VerifiedEvidencePacket(**state["evidence_packet"])
    scout = state.get("slop_audit", {})
    engine_results = state.get("engine_results", [])

    drafts = build_draft_assets(target_id, packet, scout, engine_results)
    offending = state.get("ux_lint_verdict", {}).get("ux_violations", [])
    if offending:
        drafts = _strip_violations(drafts, offending)

    plan = {
        "target_id": target_id,
        "campaign": get_campaign(target_id),
        "positioning": (
            "Contrarian mechanism-first wedge: responder biology traced to signed "
            "artifacts, anchored on AACR 2026 contrarian extraction + live engine runs."
        ),
        "channels": ["sniper_email", "social_thread", "live_demo_curl", "palmier_60s_video"],
        "evidence_policy": "every number traces to VerifiedEvidencePacket; RUO labelling enforced",
        "approval_required_before_dispatch": True,
    }
    return {"gtm_plan": plan, "draft_assets": drafts}


def ux_jargon_gate(state: GTMGraphState) -> Dict[str, Any]:
    combined = "\n\n".join(state["draft_assets"].values())
    verdict = deterministic_anti_slop_gate(state["evidence_packet"], combined)
    update: Dict[str, Any] = {"ux_lint_verdict": verdict}
    if verdict["status"] == "FAIL":
        update["ux_retry_count"] = state.get("ux_retry_count", 0) + 1
        update["offending_phrases"] = [
            v.get("phrase") or v.get("raw") for v in
            verdict["numeric_violations"] + verdict["mirage_drug_violations"] + verdict["ux_violations"]
        ]
    return update


def build_assets(state: GTMGraphState) -> Dict[str, Any]:
    manifest = build_gtm_assets_tool(
        state["target_id"],
        state["evidence_packet"],
        state["draft_assets"],
        state.get("engine_results", []),
    )
    return {"built_artifacts": manifest}


def dispatch_package(state: GTMGraphState) -> Dict[str, Any]:
    """Runs ONLY after human approval (interrupt_before). Writes the dispatch
    manifest next to the built artifacts."""
    manifest = state.get("built_artifacts", {})
    out_dir = Path(list(manifest.get("assets", {}).values())[0]).parent if manifest.get("assets") else config.CAMPAIGN_OUTPUT_DIR
    dispatch = {
        "target_id": state["target_id"],
        "approval_status": "DISPATCHED",
        "approved_by": "Alpha (human-in-the-loop interrupt)",
        "artifacts": manifest.get("assets", {}),
        "palmier_status": manifest.get("status"),
        "ruo": True,
    }
    (out_dir / "dispatch_manifest.json").write_text(json.dumps(dispatch, indent=2), encoding="utf-8")
    return {"approval_status": "DISPATCHED"}


def escalate(state: GTMGraphState) -> Dict[str, Any]:
    """Fail-closed terminal: gates could not be satisfied within MAX_RETRIES."""
    return {
        "approval_status": "ESCALATED",
        "error": (
            "Anti-slop/UX gates failed after "
            f"{MAX_RETRIES} retries; campaign NOT dispatched. "
            f"anti_slop_verdict={state.get('anti_slop_verdict', {}).get('status')}, "
            f"ux_lint_verdict={state.get('ux_lint_verdict', {}).get('status')}"
        ),
    }


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _route_after_anti_slop(state: GTMGraphState) -> str:
    verdict = state.get("anti_slop_verdict", {})
    if verdict.get("status") == "PASS":
        return "gtm_architect"
    if state.get("retry_count", 0) <= MAX_RETRIES:
        return "execute_ml"
    return "escalate"


def _route_after_ux(state: GTMGraphState) -> str:
    verdict = state.get("ux_lint_verdict", {})
    if verdict.get("status") == "PASS":
        return "build_assets"
    if state.get("ux_retry_count", 0) <= MAX_RETRIES:
        return "gtm_architect"
    return "escalate"


# --------------------------------------------------------------------------
# Graph factory
# --------------------------------------------------------------------------


def build_gtm_graph(checkpointer: Optional[SqliteSaver] = None) -> Any:
    """Compile the CrisPRO-GTM-Graph.

    checkpointer: pass a SqliteSaver for persistence + human-in-the-loop
    interrupts. If None, a fresh SqliteSaver is created at
    config.CHECKPOINT_PATH.
    """
    if checkpointer is None:
        config.CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(config.CHECKPOINT_PATH), check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    g = StateGraph(GTMGraphState)
    g.add_node("slop_scout", slop_scout)
    g.add_node("execute_ml", execute_ml)
    g.add_node("anti_slop_gate", anti_slop_gate)
    g.add_node("gtm_architect", gtm_architect)
    g.add_node("ux_jargon_gate", ux_jargon_gate)
    g.add_node("build_assets", build_assets)
    g.add_node("dispatch_package", dispatch_package)
    g.add_node("escalate", escalate)

    g.add_edge(START, "slop_scout")
    g.add_edge("slop_scout", "execute_ml")
    g.add_edge("execute_ml", "anti_slop_gate")
    g.add_conditional_edges(
        "anti_slop_gate", _route_after_anti_slop,
        {"gtm_architect": "gtm_architect", "execute_ml": "execute_ml", "escalate": "escalate"},
    )
    g.add_edge("gtm_architect", "ux_jargon_gate")
    g.add_conditional_edges(
        "ux_jargon_gate", _route_after_ux,
        {"build_assets": "build_assets", "gtm_architect": "gtm_architect", "escalate": "escalate"},
    )
    g.add_edge("build_assets", "dispatch_package")
    g.add_edge("dispatch_package", END)
    g.add_edge("escalate", END)

    return g.compile(checkpointer=checkpointer, interrupt_before=["dispatch_package"])
