"""Target campaign registry for the CrisPRO-GTM-Graph.

IMPORTANT PROVENANCE NOTE
-------------------------
``registry_identifiers`` (trial names, DOIs, NCT IDs) and narrative context
strings are USER-SUPPLIED by Fahad Kiana (campaign briefs). They are carried
as supplied and are labelled ``user_supplied_campaign_registry`` in every
evidence packet. Numbers originating here are identifiers/context only and
are never presented as computed model outputs.
"""

from __future__ import annotations

from typing import Any, Dict, List

CAMPAIGNS: Dict[str, Dict[str, Any]] = {
    "repare_camonsertib": {
        "drug_names": ["camonsertib", "RP-3500", "RP3500"],
        "primary_target": "ATR",
        "sponsor": "Repare Therapeutics",
        "context_tags": ["ATR_inhibitor", "synthetic_lethality"],
        "registry_identifiers": ["Camonsertib + gemcitabine tolerability"],
        "pitfall_keywords": ["tolerability", "dose-limiting toxicity", "combination"],
        "narrative": "ATR inhibitor; positioning turns on synthetic-lethality context and combination tolerability.",
    },
    "merck_tuvusertib": {
        "drug_names": ["tuvusertib", "M1774"],
        "primary_target": "ATR",
        "sponsor": "Merck KGaA / EMD Serono",
        "context_tags": ["ATR_inhibitor", "MBD4_only"],
        # MBD4-only context: Olaparib is a banned mirage drug in drafts for this campaign.
        "banned_drugs": ["Olaparib"],
        "registry_identifiers": [
            "DDRiver EOC 302",
            "10.64898/2026.11.10.765519",  # MBD4 bioRxiv DOI (user-supplied)
        ],
        "pitfall_keywords": ["resistance", "biomarker", "failure", "dilution"],
        "narrative": "ATR inhibitor anchored on MBD4-loss biology (bioRxiv) and the DDRiver EOC 302 trial context.",
    },
    "az_saruparib_latify": {
        "drug_names": ["saruparib", "AZD5303"],
        "primary_target": "PARP1",
        "sponsor": "AstraZeneca",
        "context_tags": ["PARP1_selective", "LATIFY_preliminary_label"],
        "registry_identifiers": ["LATIFY"],
        "pitfall_keywords": ["myeloid", "thrombocytopenia", "selectivity"],
        "narrative": "PARP1-selective inhibitor; LATIFY label status is preliminary and must be labelled as such.",
    },
    "brenus_stc1010": {
        "drug_names": ["STC-1010", "stc-1010"],
        "primary_target": "haptenated whole-cell therapeutic vaccine",
        "sponsor": "Brenus Pharma",
        "context_tags": ["MSS_CRC", "therapeutic_vaccine"],
        "registry_identifiers": ["BreAK CRC-001", "mFOLFOX6", "bevacizumab"],
        "pitfall_keywords": ["vaccine", "MSS", "immune", "cold tumor"],
        "narrative": "Haptenated whole-cell therapeutic vaccine for MSS colorectal cancer; backbone mFOLFOX6 +/- bevacizumab.",
    },
    "sanofi_ceacam5": {
        "drug_names": ["CEACAM5", "SGN-CEACAM5C", "tusamitamab", "SAR408701"],
        "primary_target": "CEACAM5",
        "sponsor": "Sanofi",
        "context_tags": ["ADC", "NSCLC"],
        "registry_identifiers": ["CARMEN-LC03"],
        "pitfall_keywords": ["ADC", "payload", "ocular toxicity", "failure"],
        "narrative": "Anti-CEACAM5 ADC franchise context; CARMEN-LC03 outcome is the contrarian anchor.",
    },
    "ayesha_clinical_brief": {
        "drug_names": [],
        "primary_target": "HGSOC tumor board",
        "sponsor": "internal",
        "context_tags": ["HGSOC", "MBD4", "tumor_board"],
        "registry_identifiers": ["AYESHA_001"],
        "pitfall_keywords": ["platinum", "resistance", "HRD"],
        "narrative": "Clinical brief for the Ayesha HGSOC tumor-board case (TP53 LOF + MBD4 LOF, MMR preserved).",
    },
}

# Drugs that must never appear in external GTM copy regardless of campaign.
GLOBAL_MIRAGE_DRUGS: List[str] = ["Vismodegib"]


def get_campaign(target_id: str) -> Dict[str, Any]:
    if target_id not in CAMPAIGNS:
        raise KeyError(
            f"Unknown target_id '{target_id}'. Known campaigns: {sorted(CAMPAIGNS)}"
        )
    return {"target_id": target_id, **CAMPAIGNS[target_id]}
