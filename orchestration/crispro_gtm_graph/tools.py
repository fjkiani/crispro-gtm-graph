"""Strike 2: typed, fail-closed tool layer for the CrisPRO-GTM-Graph.

Tools
-----
1. scout_target_and_slop_tool      -- AACR FTS5 + trial receipts + Repo Slop Patrol
2. execute_crispro_engines_tool    -- live FastAPI ML calls + VerifiedEvidencePacket
3. deterministic_anti_slop_gate    -- numeric verifier + mirage-drug + UX/voice linter
4. build_gtm_assets_tool           -- email / thread / curl / Palmier MCP package

Zero LLM-invented stats: every number in copy must trace to the packet.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel

from . import config
from . import evidence as evidence_mod
from .aacr_2026_indexer import search as aacr_search
from .campaigns import GLOBAL_MIRAGE_DRUGS, get_campaign
from .evidence import VerifiedEvidencePacket
from .engines import DEMO_PATIENT_8D, execute_all_engines

# ==========================================================================
# 1. Scout + Repo Slop Patrol
# ==========================================================================


class SlopPatrolResult(BaseModel):
    http_501_hits_gtm_critical: int
    http_501_hits_v2_legacy: int
    v2_legacy_501_files: List[str]
    unclear_strings_in_src_data: int
    receipt_sha256_checks: List[Dict[str, Any]]
    frontend_root: str
    verdict: str
    notes: List[str]


def _repo_analysis_mentions(term: str) -> List[str]:
    """Honest secondary signal: term appears in the aacr repo's derived
    analysis/outreach artifacts (not in the raw session extractions)."""
    aacr_root = config.AACR_SCHEMA_A_DIR.parents[1]
    hits: List[str] = []
    for sub in ("data/analysis", "data/outreach", "reports"):
        d = aacr_root / sub
        if not d.exists():
            continue
        for fp in [*d.rglob("*.json"), *d.rglob("*.md")][:400]:
            try:
                if term.lower() in fp.read_text(encoding="utf-8", errors="ignore").lower():
                    hits.append(str(fp.relative_to(aacr_root)))
            except OSError:
                continue
            if len(hits) >= 5:
                return hits
    return hits


def _trial_receipts(drug_names: List[str]) -> List[Dict[str, Any]]:
    db = config.V2_TRIALS_DB
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    blob = "lower(coalesce(nct_id,'')||' '||coalesce(title,'')||' '||coalesce(official_title,'')||' '||coalesce(conditions,'')||' '||coalesce(keywords,'')||' '||coalesce(interventions,'')||' '||coalesce(description_text,''))"
    out: List[Dict[str, Any]] = []
    try:
        for drug in drug_names:
            rows = conn.execute(
                f"SELECT nct_id, title, phase, status, sponsor, enrollment FROM clinical_trials "
                f"WHERE {blob} LIKE ? LIMIT 5",
                (f"%{drug.lower()}%",),
            ).fetchall()
            for r in rows:
                out.append({"matched_drug": drug, **dict(r)})
    finally:
        conn.close()
    return out


def _repo_slop_patrol() -> SlopPatrolResult:
    notes: List[str] = []
    # --- HTTP 501 scan ---
    # GTM-critical roots (v3 backend + this package) must contain ZERO 501
    # stubs. The v2 repo's legacy routers are scanned separately and reported
    # honestly: pre-existing 501 stubs there are a patrol FINDING, not a
    # silent pass.
    patterns_501 = re.compile(
        r"(HTTP\s?501|501\s+Not\s+Implemented|status_code\s*=\s*501|"
        r"HTTPStatus\.NOT_IMPLEMENTED|[\"']501[\"'])"
    )

    def _count_501(root: Path, exts: tuple) -> tuple:
        count = 0
        files: List[str] = []
        if not root.exists():
            return count, files
        for fp in root.rglob("*"):
            if fp.suffix.lstrip(".") not in exts or not fp.is_file():
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # skip the patrol's own source/tests (self-referential matches)
            if "SlopPatrolResult" in text or "/tests/" in str(fp).replace("\\", "/"):
                continue
            n = len(patterns_501.findall(text))
            if n:
                count += n
                files.append(str(fp.relative_to(root)))
        return count, files

    critical_roots = [config.CRISPRO_V3_ROOT, Path(__file__).resolve().parents[1]]
    critical_501 = 0
    for root in critical_roots:
        c, _ = _count_501(root, ("py", "ts"))
        critical_501 += c
    legacy_501, legacy_files = _count_501(config.CRISPRO_V2_ROOT / "api", ("py", "ts"))
    if legacy_501:
        notes.append(
            f"FINDING: {legacy_501} pre-existing HTTP 501 stubs in crispro-backend-v2 "
            f"legacy routers (evidence/agents/dsr/myeloma/resistance) — outside the "
            f"GTM-critical path; reported, not hidden."
        )

    # --- "UNCLEAR" strings in frontend data ---
    unclear = 0
    fe = config.CRISPRO_FRONTEND_ROOT
    src_data = fe / "src" / "data"
    scan_dir = src_data if src_data.exists() else (fe if fe.exists() else None)
    if scan_dir is None:
        notes.append(f"frontend root not found at {fe}; UNCLEAR count not computable")
    else:
        if not src_data.exists():
            notes.append(
                f"frontend repo has no src/data layout; scanned repo root data files at {fe}"
            )
        for fp in list(scan_dir.glob("*"))[:200]:
            if fp.is_file() and fp.suffix.lstrip(".") in ("csv", "md", "json", "ts", "txt"):
                try:
                    unclear += fp.read_text(encoding="utf-8", errors="ignore").count("UNCLEAR")
                except OSError:
                    continue

    # --- receipt SHA-256 checks against ARTIFACT_LOCKS ---
    checks: List[Dict[str, Any]] = []
    try:
        import sys

        v2 = str(config.CRISPRO_V2_ROOT)
        if v2 not in sys.path:
            sys.path.insert(0, v2)
        from api.ml.crispro_unified_ml import ARTIFACT_LOCKS

        for key, lock in ARTIFACT_LOCKS.items():
            path = config.V2_MODELS_DIR / lock["path"]
            entry = {"artifact": lock["path"], "lock": key, "expected": lock["sha256"]}
            if not path.exists():
                entry.update({"actual": None, "match": False, "error": "file missing"})
            else:
                actual = evidence_mod._sha256_file(path)
                entry.update({"actual": actual, "match": actual == lock["sha256"]})
            checks.append(entry)
    except Exception as exc:
        notes.append(f"ARTIFACT_LOCKS import failed: {exc}")

    all_match = bool(checks) and all(c.get("match") for c in checks)
    verdict = (
        "PASS"
        if (critical_501 == 0 and unclear == 0 and all_match)
        else "FAIL"
    )
    return SlopPatrolResult(
        http_501_hits_gtm_critical=critical_501,
        http_501_hits_v2_legacy=legacy_501,
        v2_legacy_501_files=legacy_files[:10],
        unclear_strings_in_src_data=unclear,
        receipt_sha256_checks=checks,
        frontend_root=str(fe) if fe.exists() else "ABSENT",
        verdict=verdict,
        notes=notes,
    )


def scout_target_and_slop_tool(target_id: str) -> Dict[str, Any]:
    """Scout AACR 2026 coverage + trial receipts + repo slop for a campaign."""
    campaign = get_campaign(target_id)
    aacr: Dict[str, Any] = {}
    for drug in campaign.get("drug_names", []):
        res = aacr_search(drug, limit=3)
        aacr[drug] = {"total_hits": res["total_hits"], "top_hits": res["hits"][:3]}
        if res["total_hits"] == 0:
            aacr[drug]["repo_analysis_mentions"] = _repo_analysis_mentions(drug)
    tgt = aacr_search(campaign["primary_target"], limit=3)
    aacr["primary_target"] = campaign["primary_target"]
    aacr["primary_target_hits"] = tgt["total_hits"]
    aacr["primary_target_top_hits"] = tgt["hits"][:3]
    pitfalls: Dict[str, int] = {}
    for kw in campaign.get("pitfall_keywords", []):
        pitfalls[kw] = aacr_search(kw, limit=1)["total_hits"]

    patrol = _repo_slop_patrol()
    return {
        "target_id": target_id,
        "campaign": campaign,
        "aacr": aacr,
        "pitfall_keyword_hits": pitfalls,
        "trial_receipts": _trial_receipts(campaign.get("drug_names", [])),
        "repo_slop_patrol": patrol.model_dump(),
        "honesty_note": (
            "Zero-hit drugs are reported as zero. AACR coverage reflects the session "
            "extractions only; repo analysis artifacts are listed separately when they "
            "mention a term."
        ),
    }


# ==========================================================================
# 2. Execute CrisPRO engines + build evidence packet
# ==========================================================================


def execute_crispro_engines_tool(
    target_id: str, patient_8d: Optional[List[float]] = None
) -> Dict[str, Any]:
    """Run the three ML endpoints (live HTTP -> in-process fallback) and
    assemble the fail-closed VerifiedEvidencePacket."""
    campaign = get_campaign(target_id)
    engine_results = [r.model_dump() for r in execute_all_engines(target_id, patient_8d)]
    packet = evidence_mod.build_evidence_packet(target_id)

    # Live engine outputs are real computed values (SHA-locked artifact /
    # in-process v3 pipeline) — record them as verified values so the brief
    # and any copy citing them trace to their engine, not to imagination.
    from .evidence import VerifiedValue

    for call in engine_results:
        s = call.get("summary") or {}
        if "bilinear" in call["endpoint"] and call["ok"]:
            top = (s.get("top3_moa") or [{}])[0]
            if top:
                packet.verified_values.append(
                    VerifiedValue(
                        label=f"live_bilinear_top_moa_score_{top.get('moa')}",
                        value=top.get("score"),
                        source="POST /v1/ranker/8d/bilinear (in-process Stage-2A, SHA "
                               + str(s.get("artifact_sha256", "")) + ")",
                        provenance="live_engine_output",
                    )
                )
            if s.get("mean_loto_auroc") is not None:
                packet.verified_values.append(
                    VerifiedValue(
                        label="live_bilinear_mean_loto_auroc",
                        value=s["mean_loto_auroc"],
                        source="POST /v1/ranker/8d/bilinear (in-process Stage-2A)",
                        provenance="live_engine_output",
                    )
                )
        if call["endpoint"].endswith("bundle") and call["ok"] and s.get("completeness_score") is not None:
            packet.verified_values.append(
                VerifiedValue(
                    label="live_bundle_completeness_score",
                    value=s["completeness_score"],
                    source="POST /api/ayesha/therapy-fit/bundle (in-process v3)",
                    provenance="live_engine_output",
                )
            )

    return {
        "target_id": target_id,
        "engine_results": engine_results,
        "evidence_packet": packet.model_dump(),
        "demo_patient_8d": patient_8d or DEMO_PATIENT_8D.get(target_id, DEMO_PATIENT_8D["default"]),
        "campaign": campaign,
    }


def make_evidence_brief(exec_result: Dict[str, Any]) -> str:
    """The evidence brief checked by the anti_slop_gate node (stage 1 of copy):
    a compact text rendering of the packet + engine outputs. Any hallucinated
    number injected here fails the gate before the architect writes copy."""
    packet = VerifiedEvidencePacket(**exec_result["evidence_packet"])
    lines = [f"Evidence brief for {packet.target_id} (RUO):"]
    for v in packet.verified_values:
        lines.append(f"- {v.label}: {v.value} [{v.provenance}]")
    for call in exec_result["engine_results"]:
        lines.append(f"- engine {call['endpoint']}: mode={call['mode']} ok={call['ok']}")
        if call["endpoint"].endswith("bundle") and call["ok"]:
            s = call["summary"]
            lines.append(
                f"  bundle completeness {s.get('completeness_score')} at {s.get('level')}; "
                f"boardroom outcome {s.get('boardroom_outcome')}"
            )
        if "bilinear" in call["endpoint"] and call["ok"]:
            s = call["summary"]
            lines.append(
                f"  top MoA {s['top3_moa'][0]['moa']} score {s['top3_moa'][0]['score']}; "
                f"mean LOTO AUROC {s.get('mean_loto_auroc')}"
            )
    return "\n".join(lines)


# ==========================================================================
# 3. Deterministic anti-slop gate (numeric verifier + UX/voice linter)
# ==========================================================================

BANNED_PHRASES = [
    "on disk",
    "frozen JSON",
    "UNCLEAR",
    "SPE-high",
    "Door 1",
    "Door 2",
    "put that split on the board",
]
HEADLINE_JARGON = ["AUROC", "LOTO", "bilinear", "latent"]

# Structural constants allowed in copy without packet verification
# (documented: sentence counts, video seconds, DPI, years, thread markers).
_STRUCTURAL_CONSTANTS = {5, 60, 300} | {y for y in range(2024, 2028)}

_STRIP_PATTERNS = [
    re.compile(r"\bNCT\d{2,12}\b", re.IGNORECASE),
    re.compile(r"\b10\.\d{4,9}/\S+"),          # DOIs
    re.compile(r"SHA-256", re.IGNORECASE),
    re.compile(r"GRCh38", re.IGNORECASE),
    re.compile(r"H\.264", re.IGNORECASE),
    re.compile(r"\[\d{2}:\d{2}\]"),             # VO timestamps
    re.compile(r"^\s*\d+/\s", re.MULTILINE),    # social thread post markers
]

_FRACTION_RE = re.compile(r"\b(\d{1,4})/(\d{1,4})\b")
_DECIMAL_RE = re.compile(r"\b\d+\.\d+\b")
_INT_RE = re.compile(r"\b\d+\b")


def _headline_lines(text: str) -> List[str]:
    """Primary headlines: first non-empty line, markdown headers, subject lines."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    headlines: List[str] = []
    if lines:
        headlines.append(lines[0])
    for s in lines:
        if s.startswith("#") or s.lower().startswith("subject:"):
            headlines.append(s)
    return headlines


def _extract_numbers(clean: str) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    spans: List[tuple] = []
    for m in _FRACTION_RE.finditer(clean):
        found.append({"kind": "fraction", "num": int(m.group(1)), "den": int(m.group(2)), "raw": m.group(0), "context": clean[max(0, m.start() - 40):m.end() + 40]})
        spans.append(m.span())
    for m in _DECIMAL_RE.finditer(clean):
        if any(s <= m.start() < e for s, e in spans):
            continue
        found.append({"kind": "decimal", "value": float(m.group(0)), "decimals": len(m.group(0).split(".")[1]), "raw": m.group(0), "context": clean[max(0, m.start() - 40):m.end() + 40]})
        spans.append(m.span())
    for m in _INT_RE.finditer(clean):
        if any(s <= m.start() < e for s, e in spans):
            continue
        found.append({"kind": "int", "value": int(m.group(0)), "raw": m.group(0), "context": clean[max(0, m.start() - 40):m.end() + 40]})
    return found


def _number_passes(item: Dict[str, Any], packet: VerifiedEvidencePacket) -> bool:
    verified_floats = packet.numeric_values()
    verified_strings = packet.string_values() + packet.allowed_identifiers
    if item["kind"] == "fraction":
        raw = item["raw"]
        if any(raw in s for s in verified_strings):
            return True
        return _value_passes(item["num"], verified_floats) and _value_passes(item["den"], verified_floats)
    return _value_passes(item["value"], verified_floats, item.get("decimals", 0))


def _value_passes(value: float, verified: List[float], decimals: int = 0) -> bool:
    if int(value) in _STRUCTURAL_CONSTANTS and value == int(value):
        return True
    for v in verified:
        if v == value:
            return True
        if decimals > 0 and abs(value - v) <= 0.5 * (10 ** -decimals):
            return True
        if decimals == 0 and abs(value - v) < 0.5:
            return True
    return False


def deterministic_anti_slop_gate(
    packet: VerifiedEvidencePacket | Dict[str, Any], draft_copy: str
) -> Dict[str, Any]:
    """Numeric verifier + mirage-drug check + UI/UX human-voice linter.

    FAIL-closed: any unverified number, banned mirage drug, banned phrase, or
    ML jargon in a headline => status FAIL with the exact violations listed.
    """
    if isinstance(packet, dict):
        packet = VerifiedEvidencePacket(**packet)

    # --- preprocess: strip identifiers/technical tokens before extraction ---
    clean = draft_copy
    for ident in packet.allowed_identifiers + packet.string_values():
        clean = clean.replace(ident, " ")
    for pat in _STRIP_PATTERNS:
        clean = pat.sub(" ", clean)

    numbers = _extract_numbers(clean)
    numeric_violations = [
        {"raw": n["raw"], "context": n["context"].strip()}
        for n in numbers
        if not _number_passes(n, packet)
    ]

    # --- mirage drugs ---
    mirage: List[Dict[str, str]] = []
    lowered = draft_copy.lower()
    for drug in GLOBAL_MIRAGE_DRUGS:
        if drug.lower() in lowered:
            mirage.append({"drug": drug, "rule": "globally banned mirage drug"})
    for drug in packet.banned_drugs:
        if drug.lower() in lowered:
            mirage.append(
                {
                    "drug": drug,
                    "rule": f"banned in {packet.target_id} context (context_tags={packet.context_tags})",
                }
            )

    # --- UX / human-voice linter ---
    ux_violations: List[Dict[str, str]] = []
    for phrase in BANNED_PHRASES:
        for m in re.finditer(re.escape(phrase), draft_copy, re.IGNORECASE):
            ux_violations.append({"phrase": phrase, "kind": "banned_phrase", "location": draft_copy[max(0, m.start() - 30):m.end() + 30]})
    headlines = _headline_lines(draft_copy)
    for line in headlines:
        for jargon in HEADLINE_JARGON:
            if re.search(r"\b" + re.escape(jargon) + r"\b", line, re.IGNORECASE):
                ux_violations.append({"phrase": jargon, "kind": "ml_jargon_in_headline", "location": line})

    status = "PASS" if not (numeric_violations or mirage or ux_violations) else "FAIL"
    return {
        "status": status,
        "numeric_violations": numeric_violations,
        "mirage_drug_violations": mirage,
        "ux_violations": ux_violations,
        "stats": {
            "numbers_checked": len(numbers),
            "numbers_verified": len(numbers) - len(numeric_violations),
            "verified_values_in_packet": len(packet.verified_values),
        },
    }


# ==========================================================================
# 4. Build GTM assets (email, thread, curl demo, Palmier MCP package)
# ==========================================================================


def build_gtm_assets_tool(
    target_id: str,
    packet: VerifiedEvidencePacket | Dict[str, Any],
    draft_assets: Dict[str, str],
    engine_results: List[Dict[str, Any]],
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write the complete GTM package: sniper email, social thread, live demo
    curl, Palmier timeline manifest + VO script. Returns the artifact manifest.

    The Palmier leg is delegated to palmier_bridge, which uses the
    crispro-gtm-studio kit's own PalmierMCP client and timeline grammar
    (see palmier_bridge.py docstring)."""
    if isinstance(packet, dict):
        packet = VerifiedEvidencePacket(**packet)
    run_id = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(output_dir) if output_dir else config.CAMPAIGN_OUTPUT_DIR / target_id / run_id
    out.mkdir(parents=True, exist_ok=True)

    (out / "sniper_email.md").write_text(draft_assets["sniper_email.md"], encoding="utf-8")
    (out / "social_thread.md").write_text(draft_assets["social_thread.md"], encoding="utf-8")
    (out / "vo_script.md").write_text(draft_assets["vo_script.md"], encoding="utf-8")

    demo_vector = DEMO_PATIENT_8D.get(target_id, DEMO_PATIENT_8D["default"])
    curl = f"""#!/usr/bin/env bash
# CrisPRO live API demo — {target_id} (RUO)
# Requires the v3 backend on :8000 (and v2 on :8001 if mounted).
set -euo pipefail

echo '--- 1) Ayesha tumor-board bundle (v3) ---'
curl -s -X POST "{config.CRISPRO_API_V3_URL}/api/ayesha/therapy-fit/bundle" \\
  -H 'Content-Type: application/json' -d '{{}}' | head -c 800; echo

echo '--- 2) 8D bilinear ranker (Stage-2A signed decoder) ---'
curl -s -X POST "{config.CRISPRO_API_V2_URL}/v1/ranker/8d/bilinear" \\
  -H 'Content-Type: application/json' \\
  -d '{json.dumps({"patient_8d": demo_vector})}' | head -c 800; echo

echo '--- 3) AACR 2026 KB search (v3 kb router) ---'
curl -s "{config.CRISPRO_API_V3_URL}/api/kb/aacr2026/search?q=ATR&limit=3" | head -c 800; echo
"""
    (out / "live_demo_curl.sh").write_text(curl, encoding="utf-8")
    (out / "live_demo_curl.sh").chmod(0o755)

    # Palmier leg: wired into the crispro-gtm-studio kit (client + grammar + gate)
    from . import palmier_bridge

    palmier = palmier_bridge.run_palmier_package(target_id, draft_assets["vo_script.md"])
    manifest = {
        "target_id": target_id,
        "run_id": run_id,
        "status": palmier["status"],
        "mcp_url": config.PALMIER_MCP_URL,
        "palmier": palmier,
        "queue": palmier.get("queue", []),
        "assets": {
            "sniper_email": str(out / "sniper_email.md"),
            "social_thread": str(out / "social_thread.md"),
            "vo_script": str(out / "vo_script.md"),
            "live_demo_curl": str(out / "live_demo_curl.sh"),
            "palmier_timeline_manifest": str(out / "palmier_timeline_manifest.json"),
        },
        "evidence_packet_summary": {
            "verified_values": len(packet.verified_values),
            "allowed_identifiers": packet.allowed_identifiers,
            "ruo": packet.ruo,
        },
    }
    (out / "palmier_timeline_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
