"""Palmier bridge: the CrisPRO-GTM-Graph wired into the crispro-gtm-studio kit.

This module replaces the earlier hand-rolled httpx probe. It now uses the
kit's OWN audited client (``crispro-gtm-studio/crispro-studio/scripts/mcp_client.py``
— session-aware, 404 re-init, SSE decoding, retries) and mirrors the kit's
timeline scripts (``timelines/video2_pharma_sniper.py`` grammar: 24 fps frame
math, 6-beat structure, tools/call JSON-RPC, one-target-one-render) and its
SKILL.md verification gate: trial claims must come from the target card
(``targets/{target}.json``); a missing card blocks live execution — never
improvise a trial narrative.

Campaign -> kit mapping
-----------------------
repare_camonsertib    -> skills/pharma-bd-sniper-reel, video2 --target repare
merck_tuvusertib      -> skills/pharma-bd-sniper-reel, video2 --target merck_kgaa
az_saruparib_latify   -> skills/pharma-bd-sniper-reel, video2 --target astrazeneca
brenus_stc1010        -> skills/pharma-bd-sniper-reel, video2 --target brenus
sanofi_ceacam5        -> skills/pharma-bd-sniper-reel, video2 --target sanofi
ayesha_clinical_brief -> skills/clinical-case-60s-brief, video3 --case ak
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .campaigns import get_campaign

FPS = 24
ACCENT = "#E9ED4C"  # kit accent (video2_pharma_sniper.py)

# Kit-faithful 6-beat structure (frames at 24 fps), per
# skills/pharma-bd-sniper-reel/SKILL.md and timelines/video2_pharma_sniper.py.
BEATS = [
    {"beat": 1, "name": "cold_open", "at_s": 0.0, "dur_s": 3.0},
    {"beat": 2, "name": "the_failure", "at_s": 3.0, "dur_s": 5.0},
    {"beat": 3, "name": "the_sting", "at_s": 8.0, "dur_s": 3.0},
    {"beat": 4, "name": "the_rescue", "at_s": 11.0, "dur_s": 6.0},
    {"beat": 5, "name": "proof", "at_s": 17.0, "dur_s": 4.0},
    {"beat": 6, "name": "cta", "at_s": 21.0, "dur_s": 3.0},
]

# Static overlays from the kit (beats 3 and 5 are card-independent).
STATIC_OVERLAYS = {
    "the_sting": "The responders were there. The trial couldn't see them.",
    "proof": "Mechanistic stratum, pre-treatment DNA",
}

KIT_MAP = {
    "repare_camonsertib": {"skill": "pharma-bd-sniper-reel", "script": "video2_pharma_sniper.py", "target_arg": "repare"},
    "merck_tuvusertib": {"skill": "pharma-bd-sniper-reel", "script": "video2_pharma_sniper.py", "target_arg": "merck_kgaa"},
    "az_saruparib_latify": {"skill": "pharma-bd-sniper-reel", "script": "video2_pharma_sniper.py", "target_arg": "astrazeneca"},
    "brenus_stc1010": {"skill": "pharma-bd-sniper-reel", "script": "video2_pharma_sniper.py", "target_arg": "brenus"},
    "sanofi_ceacam5": {"skill": "pharma-bd-sniper-reel", "script": "video2_pharma_sniper.py", "target_arg": "sanofi"},
    "ayesha_clinical_brief": {"skill": "clinical-case-60s-brief", "script": "video3_ayesha_clinical.py", "target_arg": "ak"},
}


def _f(seconds: float) -> int:
    """Kit frame math: f(s) = int(round(s * FPS))."""
    return int(round(seconds * FPS))


def kit_dir() -> Path:
    d = config.CRISPRO_GTM_STUDIO_DIR
    if not (d / "scripts" / "mcp_client.py").exists():
        raise FileNotFoundError(
            f"crispro-gtm-studio kit not found at {d} (expected scripts/mcp_client.py). "
            "Set CRISPRO_GTM_STUDIO_DIR."
        )
    return d


def load_palmier_client():
    """Import PalmierMCP from the kit's own mcp_client.py (audited client)."""
    path = kit_dir() / "scripts" / "mcp_client.py"
    spec = importlib.util.spec_from_file_location("crispro_studio_mcp_client", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load kit mcp_client from {path}")
    mod = importlib.util.module_from_spec(spec)
    if "crispro_studio_mcp_client" not in sys.modules:
        sys.modules["crispro_studio_mcp_client"] = mod
    spec.loader.exec_module(mod)
    return mod.PalmierMCP


def card_path(target_id: str) -> Path:
    mapping = KIT_MAP[target_id]
    if mapping["script"].startswith("video3"):
        return kit_dir() / "cases" / f"{mapping['target_arg']}.json"
    return kit_dir() / "targets" / f"{mapping['target_arg']}.json"


def write_draft_card(target_id: str) -> Optional[Path]:
    """Write a DRAFT target-card skeleton from campaign registry identifiers.

    The kit's gate requires a human-curated card before live rendering; the
    draft carries only user-supplied registry identifiers and explicit nulls —
    it never invents trial claims. Returns the draft path (or None)."""
    campaign = get_campaign(target_id)
    mapping = KIT_MAP[target_id]
    dest = card_path(target_id).with_suffix(".draft.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    draft = {
        "_status": "DRAFT — needs human curation per SKILL.md before live render",
        "company": campaign.get("sponsor"),
        "salutation": f"{campaign.get('sponsor', 'team')} team",
        "trial": {
            "name": campaign.get("registry_identifiers", [None])[0],
            "nct": None,
            "regimen": ", ".join(campaign.get("drug_names", [])) or None,
            "failure_mode": None,
            "responder_evidence": None,
        },
        "rescue_view": "captures/REPLACE_ME.mp4",
        "proof_figure": "figures/REPLACE_ME_300dpi.png",
        "responder_stratum": None,
    }
    dest.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    return dest


def build_queue(target_id: str, vo_script: str, card: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Build the MCP call queue in the kit's own grammar (tools/call JSON-RPC,
    24 fps durationFrames/atFrame, 6 beats, one-target-one-render)."""
    mapping = KIT_MAP[target_id]
    project = (
        f"case-{mapping['target_arg']}-60s"
        if mapping["script"].startswith("video3")
        else f"sniper-{mapping['target_arg']}-60s"
    )
    trial = (card or {}).get("trial", {})
    salutation = (card or {}).get("salutation") or "your team"

    overlays: List[Dict[str, Any]] = []
    for beat in BEATS:
        entry = {
            "beat": beat["beat"],
            "atFrame": _f(beat["at_s"]),
            "durationFrames": _f(beat["dur_s"]),
        }
        if beat["name"] == "cold_open":
            entry["text"] = f"{trial.get('name', 'REQUIRES_CARD')}: what the data actually said."
        elif beat["name"] == "the_failure":
            entry["text"] = (trial.get("failure_mode") or "REQUIRES_CARD")[:80]
        elif beat["name"] == "the_rescue":
            entry["text"] = f"CrisPRO rescue: {(card or {}).get('responder_stratum', 'REQUIRES_CARD')}"
            entry["style"] = {"color": ACCENT}
        elif beat["name"] == "cta":
            entry["text"] = f"{salutation} — one API call. crispro.bio/bd"
            entry["style"] = {"color": ACCENT}
        else:
            entry["text"] = STATIC_OVERLAYS[beat["name"]]
        overlays.append(entry)

    vo_lines = [l.strip("[] ") for l in vo_script.strip().splitlines() if l.strip()]

    queue: List[Dict[str, Any]] = [
        {"step": "manage_project create", "tool": "manage_project",
         "args": {"action": "create", "name": project, "fps": FPS, "aspectRatio": "9:16"}},
        {"step": "get_timeline", "tool": "get_timeline", "args": {}},
        {"step": "import_media failure_slide", "tool": "import_media",
         "args": {"source": {"path": "REQUIRES_CARD: failure_slide"}}, "requires_card": True},
        {"step": "import_media rescue_view", "tool": "import_media",
         "args": {"source": {"path": (card or {}).get("rescue_view", "REQUIRES_CARD")}}, "requires_card": True},
        {"step": "import_media proof_fig", "tool": "import_media",
         "args": {"source": {"path": (card or {}).get("proof_figure", "REQUIRES_CARD")}}, "requires_card": True},
        {"step": "add_clips 3 beats", "tool": "add_clips",
         "args": {"entries": [
             {"mediaRef": "failure_slide", "durationFrames": _f(5)},
             {"mediaRef": "rescue_view", "durationFrames": _f(6)},
             {"mediaRef": "proof_fig", "durationFrames": _f(4)},
         ]}},
        {"step": "add_texts 6 overlays", "tool": "add_texts", "args": {"entries": overlays}},
        {"step": "add_captions from VO script", "tool": "add_captions",
         "args": {"maxWords": 4, "highlightColor": ACCENT, "lines": vo_lines}},
        {"step": "export_project h264", "tool": "export_project",
         "args": {"mode": "video", "codec": "h264", "quality": "high"}},
        {"step": "manage_exports poll", "tool": "manage_exports", "args": {"action": "list"}},
        {"step": "manage_project close (one target, one render)", "tool": "manage_project",
         "args": {"action": "close"}},
    ]
    return queue


def execute_live(target_id: str, vo_script: str, card: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the queue through the kit's PalmierMCP client (live Palmier Pro)."""
    PalmierMCP = load_palmier_client()
    queue = build_queue(target_id, vo_script, card)
    receipts: List[Dict[str, Any]] = []
    mcp = PalmierMCP(url=config.PALMIER_MCP_URL)
    try:
        for step in queue:
            res = mcp.call(step["tool"], step["args"])
            receipts.append({"step": step["step"], "ok": True,
                             "result": res if isinstance(res, (str, int, float)) else "ok"})
        return {"status": "live", "mcp_url": config.PALMIER_MCP_URL,
                "client": "crispro-gtm-studio/scripts/mcp_client.py (PalmierMCP)",
                "receipts": receipts}
    finally:
        mcp.close()


def run_palmier_package(target_id: str, vo_script: str) -> Dict[str, Any]:
    """Entry point used by build_gtm_assets_tool.

    Live path: kit client + curated target card (SKILL.md gate).
    Dry-run path: kit-faithful queue + draft card skeleton + exact CLI command.
    """
    mapping = KIT_MAP[target_id]
    try:
        kit_root = kit_dir()
    except FileNotFoundError as exc:
        return {
            "target_id": target_id,
            "status": "unavailable",
            "mcp_url": config.PALMIER_MCP_URL,
            "reason": f"crispro-gtm-studio kit not reachable: {exc}",
            "queue": [],
        }
    cpath = card_path(target_id)
    card: Optional[Dict[str, Any]] = None
    if cpath.exists():
        card = json.loads(cpath.read_text(encoding="utf-8"))

    draft_path: Optional[Path] = None
    if card is None:
        draft_path = write_draft_card(target_id)

    manifest: Dict[str, Any] = {
        "target_id": target_id,
        "kit": {
            "root": str(kit_root),
            "skill": mapping["skill"],
            "timeline_script": f"timelines/{mapping['script']}",
            "cli": (
                f"python3 timelines/{mapping['script']} "
                + (f"--case {mapping['target_arg']}" if mapping["script"].startswith("video3")
                   else f"--target {mapping['target_arg']}")
            ),
            "client": "scripts/mcp_client.py (PalmierMCP, session-aware)",
        },
        "card": {
            "path": str(cpath),
            "present": card is not None,
            "draft": str(draft_path) if draft_path else None,
            "gate": "SKILL.md verification gate: trial claims come from the card only; "
                    "missing card blocks live render — never improvise a trial narrative",
        },
        "queue_grammar": "tools/call JSON-RPC, 24 fps, durationFrames/atFrame, 6 beats",
    }

    if card is None:
        manifest.update({
            "status": "dry_run",
            "mcp_url": config.PALMIER_MCP_URL,
            "reason": f"target card missing at {cpath}; draft skeleton written — curate it per SKILL.md, then rerun",
            "queue": build_queue(target_id, vo_script, None),
        })
        return manifest

    # Card present: try live through the kit client; Palmier Pro down -> dry_run
    try:
        return {**manifest, **execute_live(target_id, vo_script, card)}
    except Exception as exc:
        manifest.update({
            "status": "dry_run",
            "mcp_url": config.PALMIER_MCP_URL,
            "reason": f"live execution via kit PalmierMCP failed: {type(exc).__name__}: {exc}",
            "queue": build_queue(target_id, vo_script, card),
        })
        return manifest
