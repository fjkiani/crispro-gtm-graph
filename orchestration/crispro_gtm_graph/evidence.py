"""VerifiedEvidencePacket: every number used in GTM copy must trace here.

Fail-closed evidence loading. Sources, in priority order:

1. ``publications/{00-mbd4,04-mm,12-brm,15-ovarian,16-immunotherapy}/``
   (macOS host only) -- verified values + 300-DPI figure paths.
2. Frozen SHA-256-locked receipts in crispro-backend-v2
   (``api/resources/models/``): bilinear decoder, Diamond SAE 3-level
   validation receipt, MBD4 NEXT-pathway SAE trajectory receipt.
3. v3 ayesha fixtures (AYESHA_001 profile) for the clinical-brief campaign.
4. Campaign registry identifiers (trial names / DOIs) -- USER-SUPPLIED by
   Fahad Kiana, labelled as such; never presented as computed outputs.

No LLM-invented stats: if none of these sources can produce a value, the
packet simply does not contain it and the anti-slop gate will reject any
draft that uses it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from . import config
from .campaigns import get_campaign


class VerifiedValue(BaseModel):
    label: str
    value: Union[float, int, str]
    source: str
    sha256: Optional[str] = None
    provenance: str  # frozen_receipt | signed_artifact | fixture | user_supplied_campaign_registry | publication


class VerifiedEvidencePacket(BaseModel):
    target_id: str
    verified_values: List[VerifiedValue] = Field(default_factory=list)
    allowed_identifiers: List[str] = Field(default_factory=list)
    allowed_drugs: List[str] = Field(default_factory=list)
    banned_drugs: List[str] = Field(default_factory=list)
    context_tags: List[str] = Field(default_factory=list)
    figure_paths: List[str] = Field(default_factory=list)
    ruo: bool = True
    provenance_notes: List[str] = Field(default_factory=list)

    def numeric_values(self) -> List[float]:
        return [float(v.value) for v in self.verified_values if isinstance(v.value, (int, float))]

    def string_values(self) -> List[str]:
        return [str(v.value) for v in self.verified_values if isinstance(v.value, str)]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifact_locks() -> Dict[str, Any]:
    """Import ARTIFACT_LOCKS from the v2 repo (module import is dependency-free)."""
    import sys

    v2 = str(config.CRISPRO_V2_ROOT)
    if v2 not in sys.path:
        sys.path.insert(0, v2)
    from api.ml.crispro_unified_ml import ARTIFACT_LOCKS  # noqa: WPS433

    return ARTIFACT_LOCKS


def load_receipt_values(target_id: str) -> List[VerifiedValue]:
    """Extract verified values from the frozen v2 receipts (SHA-256 recorded)."""
    values: List[VerifiedValue] = []
    models = config.V2_MODELS_DIR
    if not models.exists():
        return values

    def add(label: str, value: Any, source: Path, provenance: str = "frozen_receipt") -> None:
        values.append(
            VerifiedValue(
                label=label,
                value=value,
                source=str(source),
                sha256=_sha256_file(source),
                provenance=provenance,
            )
        )

    # --- Stage-2A signed bilinear decoder ---
    decoder_path = models / "crispro_8d_bilinear_decoder_v1.json"
    if decoder_path.exists():
        dec = json.loads(decoder_path.read_text(encoding="utf-8"))
        summary = dec.get("loto_validation_summary") or {}
        add("bilinear_mean_loto_cohort_auroc", summary.get("mean_loto_cohort_auroc"), decoder_path, "signed_artifact")
        add("bilinear_total_patient_cohorts", summary.get("total_patient_cohorts_evaluated"), decoder_path, "signed_artifact")
        add("bilinear_subgroup_hr_sign_concordance", summary.get("subgroup_hr_sign_concordance"), decoder_path, "signed_artifact")
        add("bilinear_moa_classes", len(dec.get("moa_classes_15") or []), decoder_path, "signed_artifact")
        add("bilinear_signed_axes", len(dec.get("signed_axes_8d") or []), decoder_path, "signed_artifact")

    # --- Diamond SAE 3-level validation receipt ---
    diamond_path = models / "diamond_sae_3level_validation_receipt.json"
    if diamond_path.exists():
        dia = json.loads(diamond_path.read_text(encoding="utf-8"))
        add("diamond_benchmark_id", dia.get("benchmark_id"), diamond_path)
        l3 = dia.get("level_3_clinical_and_gdsc2_validation") or {}
        for key, blob in l3.items():
            if isinstance(blob, dict):
                if "auroc" in blob:
                    add(f"diamond_l3_{key}_auroc", blob["auroc"], diamond_path)
                for nkey in ("n_pathogenic", "n_control", "n_pathogenic_snv", "n_control_snv"):
                    if nkey in blob:
                        add(f"diamond_l3_{key}_{nkey}", blob[nkey], diamond_path)

    # --- MBD4 NEXT-pathway SAE trajectory receipt ---
    mbd4_path = models / "mbd4_next_pathway_sae_trajectory_receipt.json"
    if mbd4_path.exists():
        traj = json.loads(mbd4_path.read_text(encoding="utf-8"))
        add("mbd4_trajectory_n_variants_scored_live", traj.get("n_variants_scored_live"), mbd4_path)
        req = traj.get("scientific_requirements") or {}
        if "min_pathogenic_live_target" in req:
            add("mbd4_min_pathogenic_live_target", req["min_pathogenic_live_target"], mbd4_path)
        if "min_synonymous_live_target" in req:
            add("mbd4_min_synonymous_live_target", req["min_synonymous_live_target"], mbd4_path)
        for entry in (traj.get("door_hazard_ranking") or [])[:6]:
            if isinstance(entry, dict) and "Evolutionary_Escape_Hazard" in entry:
                add(
                    f"mbd4_escape_hazard_{entry.get('gene')}",
                    entry["Evolutionary_Escape_Hazard"],
                    mbd4_path,
                )

    # --- ARTIFACT_LOCKS metric strings (in-repo governance metadata) ---
    try:
        locks = _artifact_locks()
        locks_src = config.CRISPRO_V2_ROOT / "api" / "ml" / "crispro_unified_ml.py"
        for key, blob in locks.items():
            for field in ("metric", "disk_oof_auroc", "disk_oof_ece", "disk_hit_drug50"):
                if field in blob:
                    add(f"artifact_lock_{key}_{field}", str(blob[field]), locks_src, "frozen_receipt")
    except Exception:  # fail-open on metadata only; core receipts already loaded
        pass

    return values


def load_publication_values(target_id: str) -> tuple[List[VerifiedValue], List[str], List[str]]:
    """Load verified values + 300-DPI figure paths from publications/ (macOS host).

    Returns (values, figure_paths, notes). Absent in sandbox -> empty + note.
    """
    values: List[VerifiedValue] = []
    figures: List[str] = []
    notes: List[str] = []
    pubs = config.PUBLICATIONS_DIR
    expected = ["00-mbd4", "04-mm", "12-brm", "15-ovarian", "16-immunotherapy"]
    if not pubs.exists():
        notes.append(
            f"publications/ dir not found at {pubs} (macOS-only); "
            "packet falls back to frozen in-repo receipts. No publication "
            "figures available."
        )
        return values, figures, notes
    for sub in expected:
        d = pubs / sub
        if not d.exists():
            notes.append(f"publication dir missing: {d}")
            continue
        for jf in d.glob("*.json"):
            try:
                blob = json.loads(jf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            vv = blob.get("verified_values") if isinstance(blob, dict) else None
            if isinstance(vv, list):
                for item in vv:
                    values.append(
                        VerifiedValue(
                            label=item.get("label", f"{sub}:{jf.name}"),
                            value=item.get("value"),
                            source=str(jf),
                            sha256=_sha256_file(jf),
                            provenance="publication",
                        )
                    )
        figures.extend(str(p) for p in d.glob("*300*dpi*.png"))
        figures.extend(str(p) for p in d.glob("figures/*.png"))
    return values, figures, notes


def load_fixture_values(target_id: str) -> List[VerifiedValue]:
    """Ayesha clinical-brief campaign: verified values from the v3 fixture."""
    values: List[VerifiedValue] = []
    if target_id != "ayesha_clinical_brief":
        return values
    profile = config.V3_AYESHA_FIXTURES / "profile.json"
    if not profile.exists():
        return values
    blob = json.loads(profile.read_text(encoding="utf-8"))
    bm = blob.get("biomarkers") or {}
    values.append(VerifiedValue(label="ayesha_pdl1_cps", value=bm.get("pdl1_cps"), source=str(profile), sha256=_sha256_file(profile), provenance="fixture"))
    values.append(VerifiedValue(label="ayesha_stage", value=blob.get("stage"), source=str(profile), sha256=_sha256_file(profile), provenance="fixture"))
    return values


def build_evidence_packet(target_id: str) -> VerifiedEvidencePacket:
    """Assemble the fail-closed evidence packet for a campaign."""
    campaign = get_campaign(target_id)
    notes: List[str] = []

    receipt_values = load_receipt_values(target_id)
    pub_values, figures, pub_notes = load_publication_values(target_id)
    fixture_values = load_fixture_values(target_id)
    notes.extend(pub_notes)

    registry_values = [
        VerifiedValue(
            label=f"registry_identifier:{ident}",
            value=ident,
            source="orchestration/crispro_gtm_graph/campaigns.py",
            provenance="user_supplied_campaign_registry",
        )
        for ident in campaign.get("registry_identifiers", [])
    ]

    allowed_drugs = list(campaign.get("drug_names", []))
    if target_id == "ayesha_clinical_brief":
        allowed_drugs += ["carboplatin", "paclitaxel"]

    return VerifiedEvidencePacket(
        target_id=target_id,
        verified_values=receipt_values + pub_values + fixture_values + registry_values,
        allowed_identifiers=list(campaign.get("registry_identifiers", [])),
        allowed_drugs=allowed_drugs,
        banned_drugs=list(campaign.get("banned_drugs", [])),
        context_tags=list(campaign.get("context_tags", [])),
        figure_paths=figures,
        ruo=True,
        provenance_notes=notes,
    )
