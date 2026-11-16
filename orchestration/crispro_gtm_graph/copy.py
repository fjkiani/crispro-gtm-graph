"""Deterministic GTM copy generation.

Every number in generated copy is pulled from the VerifiedEvidencePacket by
label (fail-closed: a missing label omits the clause rather than inventing a
stat). No LLM is involved in drafting; the LangGraph retry loop exists to
catch and strip violations, not to improvise new numbers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .campaigns import get_campaign
from .evidence import VerifiedEvidencePacket


def _num(packet: VerifiedEvidencePacket, label: str) -> Optional[Any]:
    for v in packet.verified_values:
        if v.label == label:
            return v.value
    return None


def _top_moa(engine_results: List[Dict[str, Any]]) -> Optional[str]:
    for call in engine_results:
        if "bilinear" in call.get("endpoint", ""):
            top3 = (call.get("summary") or {}).get("top3_moa") or []
            if top3:
                return top3[0]["moa"].replace("_", " ")
    return None


def _atr_hook(scout: Dict[str, Any]) -> str:
    hits = (scout.get("aacr") or {}).get("primary_target_hits") or 0
    if hits and hits > 0:
        return (
            "ATR biology was a live flashpoint across the AACR 2026 session index "
            "(real speaker and pitfall hits on file), and the contrarian extraction "
            "schema flags exactly where ATR combinations dilute."
        )
    return (
        "The AACR 2026 session index shows no direct coverage of this compound — "
        "an open lane, but also zero borrowed authority: positioning has to stand "
        "on mechanism, not meeting buzz."
    )


def build_sniper_email(
    target_id: str, packet: VerifiedEvidencePacket, scout: Dict[str, Any], engine_results: List[Dict[str, Any]]
) -> str:
    c = get_campaign(target_id)
    drug = c["drug_names"][0] if c.get("drug_names") else c["primary_target"]
    sponsor = c.get("sponsor", "")
    identifiers = ", ".join(packet.allowed_identifiers[:2]) or "the current program"
    top_moa = _top_moa(engine_results) or "the mechanism-matched class"
    hook = _atr_hook(scout)
    return f"""Subject: {drug} positioning — the MBD4/ATR angle nobody is pitching

Dear {sponsor} pipeline team,

You are advancing {drug} ({identifiers}) in a field where every deck reads the same, and {hook}.

On our side, the signed CrisPRO engine ranks {top_moa} first for MBD4-loss contexts, with every score traceable to a SHA-256-locked artifact rather than a slide-ware claim.

The same pipeline surfaces which AACR 2026 speakers are already admitting the failure modes your competitors are ignoring — that is the wedge for a data-room conversation, not another efficacy recap.

I can show the live API trace for this exact indication context in fifteen minutes; reply and I will send the demo payload.

Research use only — RUO.
"""


def build_social_thread(
    target_id: str, packet: VerifiedEvidencePacket, scout: Dict[str, Any], engine_results: List[Dict[str, Any]]
) -> str:
    c = get_campaign(target_id)
    drug = c["drug_names"][0] if c.get("drug_names") else c["primary_target"]
    auroc = _num(packet, "bilinear_mean_loto_cohort_auroc")
    concord = _num(packet, "bilinear_subgroup_hr_sign_concordance")
    n_live = _num(packet, "mbd4_trajectory_n_variants_scored_live")
    top_moa = _top_moa(engine_results) or "the mechanism-matched class"

    auroc_line = (
        f"Internal validation of the signed decoder: mean leave-one-trial-out cohort discrimination {auroc}, "
        f"subgroup hazard-ratio sign concordance {concord}."
        if auroc is not None and concord is not None
        else "Internal validation receipts are on file, SHA-256 locked."
    )
    mbd4_line = (
        f"The MBD4 NEXT-pathway scan scored {n_live} variants live against the interpretability layer — "
        "the resistance door is measurable before the clinic finds it."
        if n_live is not None
        else "The MBD4 NEXT-pathway scan receipts are on file."
    )

    return f"""1/ Everyone is pitching {drug} the same way. The MBD4 data says the interesting story is elsewhere.

2/ ATR inhibition is being sold as a combination workhorse. The AACR 2026 contrarian extraction flags the dilution risk every time a speaker hedges on biomarker selection.

3/ {auroc_line}

4/ {mbd4_line}

5/ Our engine ranks {top_moa} first for MBD4-loss contexts — not because a model said so, but because the artifact is signed, locked, and reproducible.

6/ The contrarian take: the winner in this class is the team that can show WHY a responder responds. That capability is buildable today. RUO.
"""


def build_vo_script(
    target_id: str, packet: VerifiedEvidencePacket, scout: Dict[str, Any], engine_results: List[Dict[str, Any]]
) -> str:
    c = get_campaign(target_id)
    drug = c["drug_names"][0] if c.get("drug_names") else c["primary_target"]
    top_moa = _top_moa(engine_results) or "the mechanism-matched class"
    return f"""[00:00] Every pharma deck in this class looks the same. Here is the one that does not.

[00:05] {drug}: an {c.get('primary_target', 'mechanism')}-anchored program where the interesting question is not efficacy — it is responder biology.

[00:15] CrisPRO reads that biology directly: a signed engine ranks {top_moa} first for MBD4-loss contexts, and every number traces to a locked receipt.

[00:30] The same pipeline mined the AACR 2026 sessions for what speakers admitted off-script: failure modes, dilution risk, ghost responders.

[00:45] The result is a positioning wedge nobody else can copy — because it is not a claim, it is a trace.

[00:55] Research use only. Book the demo.
"""


def build_draft_assets(
    target_id: str, packet: VerifiedEvidencePacket, scout: Dict[str, Any], engine_results: List[Dict[str, Any]]
) -> Dict[str, str]:
    return {
        "sniper_email.md": build_sniper_email(target_id, packet, scout, engine_results),
        "social_thread.md": build_social_thread(target_id, packet, scout, engine_results),
        "vo_script.md": build_vo_script(target_id, packet, scout, engine_results),
    }
