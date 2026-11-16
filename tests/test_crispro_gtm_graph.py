"""Strike 4: end-to-end tests for the CrisPRO-GTM-Graph.

Covers:
  1. AACR 2026 FTS5 index — real speaker/pitfall hits + honest zero-hit drugs
  2. Repo Slop Patrol + trial receipts
  3. Anti-slop gate: injected violations ("on disk", "19/19", "Vismodegib")
  4. Conditional retry loops (anti_slop FAIL -> execute_ml; ux FAIL -> architect)
     and fail-closed ESCALATED after max retries
  5. Full E2E on merck_tuvusertib pausing at interrupt_before=["dispatch_package"]
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from orchestration.crispro_gtm_graph import aacr_2026_indexer, config
from orchestration.crispro_gtm_graph import graph as gtm_graph
from orchestration.crispro_gtm_graph import tools as gtm_tools
from orchestration.crispro_gtm_graph.evidence import VerifiedEvidencePacket

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def index_db(tmp_path_factory):
    """Build a fresh hermetic index once for the test session."""
    db = tmp_path_factory.mktemp("kb") / "aacr_2026_index.sqlite"
    stats = aacr_2026_indexer.build_index(db_path=db)
    return db, stats


@pytest.fixture(scope="session")
def engine_result():
    """Real engine execution for merck_tuvusertib (in-process fallbacks)."""
    return gtm_tools.execute_crispro_engines_tool("merck_tuvusertib")


@pytest.fixture()
def packet(engine_result):
    return VerifiedEvidencePacket(**engine_result["evidence_packet"])


def _fresh_graph(tmp_path):
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    db = tmp_path / "ckpt.sqlite"
    saver = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    return gtm_graph.build_gtm_graph(checkpointer=saver)


# ---------------------------------------------------------------------------
# 1. FTS5 index
# ---------------------------------------------------------------------------


class TestAACRIndex:
    def test_real_counts(self, index_db):
        _, stats = index_db
        # Real, honest counts from the actual extraction files
        assert stats["schema_a_files"] == 264
        assert stats["schema_b_files"] == 266
        assert stats["talks_indexed"] > 900
        assert stats["talks_joined_a_and_b"] >= 800
        assert stats["distinct_speakers"] > 850
        assert stats["distinct_nct_ids"] > 100

    @pytest.mark.parametrize("query", ["WEE1", "ATR", "CDK2", "PKMYT1", "myCAF", "TOP1 R364H", "dasatinib", "olaparib"])
    def test_directive_queries_have_hits(self, index_db, query):
        db, _ = index_db
        res = aacr_2026_indexer.search(query, db_path=db, limit=3)
        assert res["total_hits"] > 0, f"expected hits for {query}"
        assert res["hits"], "hits list should not be empty when total_hits > 0"

    def test_top1_r364h_returns_real_talk_rows(self, index_db):
        db, _ = index_db
        res = aacr_2026_indexer.search("TOP1 R364H", db_path=db, limit=5)
        assert res["total_hits"] > 0
        hit = res["hits"][0]
        assert hit["talk_id"], "hit must carry a real talk_id"
        assert hit["snip"], "hit must carry an FTS5 snippet"

    @pytest.mark.parametrize("query", ["camonsertib", "tuvusertib", "saruparib", "daraxonrasib", "stc-1010"])
    def test_zero_coverage_drugs_reported_honestly(self, index_db, query):
        db, _ = index_db
        res = aacr_2026_indexer.search(query, db_path=db, limit=3)
        assert res["total_hits"] == 0, f"{query} has no AACR 2026 session coverage; must report zero, not invent"
        assert res["hits"] == []

    def test_speaker_and_nct_categories(self, index_db):
        db, _ = index_db
        res = aacr_2026_indexer.search("NCT", category="nct", db_path=db, limit=3)
        assert res["total_hits"] >= 0  # category filter executes without error
        res2 = aacr_2026_indexer.search("a", category="speaker", db_path=db, limit=3)
        assert res2["total_hits"] >= 0


# ---------------------------------------------------------------------------
# 2. Scout + Slop Patrol
# ---------------------------------------------------------------------------


class TestScout:
    def test_scout_merck_tuvusertib(self):
        scout = gtm_tools.scout_target_and_slop_tool("merck_tuvusertib")
        assert scout["aacr"]["tuvusertib"]["total_hits"] == 0  # honest zero
        assert scout["aacr"]["primary_target_hits"] > 0        # ATR is covered
        ncts = {t["nct_id"] for t in scout["trial_receipts"]}
        assert "NCT05950464" in ncts  # real ClinicalTrials receipt for M1774/tuvusertib

    def test_repo_slop_patrol(self):
        patrol = gtm_tools._repo_slop_patrol()
        # GTM-critical stack (v3 backend + this package): zero 501 stubs allowed
        assert patrol.http_501_hits_gtm_critical == 0, "no HTTP 501 stubs allowed in the GTM-critical stack"
        # v2 legacy routers: pre-existing 501 stubs are a reported FINDING, never hidden
        assert patrol.http_501_hits_v2_legacy >= 0
        if patrol.http_501_hits_v2_legacy:
            assert patrol.v2_legacy_501_files, "legacy 501 files must be listed when count > 0"
            assert any("501" in n for n in patrol.notes), "legacy 501s must be surfaced in patrol notes"
        assert patrol.unclear_strings_in_src_data == 0, "no 'UNCLEAR' strings in frontend data"
        assert patrol.receipt_sha256_checks, "SHA-256 receipt checks must run"
        assert all(c["match"] for c in patrol.receipt_sha256_checks), "all frozen artifacts must match their locks"
        assert patrol.verdict == "PASS"


# ---------------------------------------------------------------------------
# 3. Anti-slop gate: injected violations
# ---------------------------------------------------------------------------


CLEAN_DRAFT = """Subject: tuvusertib positioning — the MBD4/ATR angle

Our signed decoder shows mean cohort discrimination 0.8078 with subgroup
concordance 12/12, and the MBD4 NEXT-pathway scan scored 52 variants live.
Research use only.
"""

VIOLATION_DRAFT = """Subject: tuvusertib — the data is on disk in a frozen JSON

Our engine went 19/19 in validation and Vismodegib is the obvious comparator;
the UNCLEAR responder split means we put that split on the board.
"""


class TestAntiSlopGate:
    def test_clean_draft_passes(self, packet):
        verdict = gtm_tools.deterministic_anti_slop_gate(packet, CLEAN_DRAFT)
        assert verdict["status"] == "PASS", verdict
        assert verdict["numeric_violations"] == []

    def test_injected_violations_all_caught(self, packet):
        verdict = gtm_tools.deterministic_anti_slop_gate(packet, VIOLATION_DRAFT)
        assert verdict["status"] == "FAIL"
        # numeric: 19/19 is not a verified fraction
        assert any(v["raw"] == "19/19" for v in verdict["numeric_violations"])
        # mirage drug: Vismodegib is globally banned
        assert any(v["drug"] == "Vismodegib" for v in verdict["mirage_drug_violations"])
        # UX: banned phrases caught
        phrases = {v["phrase"] for v in verdict["ux_violations"]}
        assert {"on disk", "frozen JSON", "UNCLEAR", "put that split on the board"} <= phrases

    def test_olaparib_banned_in_mbd4_only_context(self, packet):
        assert "MBD4_only" in packet.context_tags
        verdict = gtm_tools.deterministic_anti_slop_gate(
            packet, "Olaparib is the comparator of choice for this program."
        )
        assert any(v["drug"] == "Olaparib" for v in verdict["mirage_drug_violations"])

    def test_ml_jargon_rejected_in_headline_only(self, packet):
        headline_jargon = "Subject: Our engine hits AUROC 0.81 in MBD4-loss models"
        verdict = gtm_tools.deterministic_anti_slop_gate(packet, headline_jargon)
        assert any(v["kind"] == "ml_jargon_in_headline" and v["phrase"] == "AUROC" for v in verdict["ux_violations"])
        body_jargon = (
            "Subject: MBD4-loss responder biology, traced\n\n"
            "Body detail for the technical reader: internal validation AUROC 0.8078."
        )
        verdict2 = gtm_tools.deterministic_anti_slop_gate(packet, body_jargon)
        assert not any(v["kind"] == "ml_jargon_in_headline" for v in verdict2["ux_violations"])

    def test_unverified_number_rejected(self, packet):
        verdict = gtm_tools.deterministic_anti_slop_gate(
            packet, "Our engine achieved 0.9999 discrimination across cohorts."
        )
        assert any(v["raw"] == "0.9999" for v in verdict["numeric_violations"])

    def test_verified_rounding_accepted(self, packet):
        verdict = gtm_tools.deterministic_anti_slop_gate(
            packet, "Mean cohort discrimination was 0.81 in validation."
        )
        assert verdict["numeric_violations"] == []


# ---------------------------------------------------------------------------
# 4. Conditional retry loops
# ---------------------------------------------------------------------------


class TestRetryLoops:
    def test_anti_slop_fail_routes_to_execute_ml_then_passes(self, tmp_path, monkeypatch):
        # sabotage the brief builder: inject violations on the first pass only
        real_brief = gtm_tools.make_evidence_brief
        brief_calls = {"n": 0}

        def sabotaged_brief(exec_result):
            text = real_brief(exec_result)
            brief_calls["n"] += 1
            if brief_calls["n"] == 1:
                text += "\n- injected: validation went 19/19 and Vismodegib beat everything (on disk)"
            return text

        monkeypatch.setattr(gtm_graph, "make_evidence_brief", sabotaged_brief)

        graph = _fresh_graph(tmp_path)
        cfg = {"configurable": {"thread_id": "retry-anti-" + str(uuid.uuid4())}}
        state = graph.invoke(
            {"target_id": "merck_tuvusertib", "retry_count": 0, "ux_retry_count": 0,
             "approval_status": "AWAITING_APPROVAL"}, cfg,
        )
        # first anti_slop FAIL -> retry -> second PASS -> proceeds to interrupt
        assert state["retry_count"] == 1
        assert state["anti_slop_verdict"]["status"] == "PASS"
        assert state["ux_lint_verdict"]["status"] == "PASS"
        snap = graph.get_state(cfg)
        assert "dispatch_package" in snap.next  # paused at the human gate

    def test_persistent_violations_escalate_fail_closed(self, tmp_path, monkeypatch):
        real_brief = gtm_tools.make_evidence_brief

        def always_bad(exec_result):
            return real_brief(exec_result) + "\n- injected: 19/19 with Vismodegib on disk"

        monkeypatch.setattr(gtm_graph, "make_evidence_brief", always_bad)
        graph = _fresh_graph(tmp_path)
        cfg = {"configurable": {"thread_id": "esc-" + str(uuid.uuid4())}}
        state = graph.invoke(
            {"target_id": "merck_tuvusertib", "retry_count": 0, "ux_retry_count": 0,
             "approval_status": "AWAITING_APPROVAL"}, cfg,
        )
        assert state["approval_status"] == "ESCALATED"
        assert state["retry_count"] == gtm_graph.MAX_RETRIES + 1
        assert "NOT dispatched" in state["error"]
        assert state.get("built_artifacts") is None  # never reached asset build

    def test_ux_fail_routes_back_to_architect(self, tmp_path, monkeypatch):
        from orchestration.crispro_gtm_graph import copy as gtm_copy

        real_build = gtm_copy.build_draft_assets
        calls = {"n": 0}

        def sabotaged_build(target_id, packet, scout, engine_results):
            drafts = real_build(target_id, packet, scout, engine_results)
            calls["n"] += 1
            if calls["n"] == 1:
                drafts = dict(drafts)
                drafts["sniper_email.md"] += "\n\nP.S. the receipts live on disk in a frozen JSON (19/19 checks)."
            return drafts

        monkeypatch.setattr(gtm_graph, "build_draft_assets", sabotaged_build)
        graph = _fresh_graph(tmp_path)
        cfg = {"configurable": {"thread_id": "retry-ux-" + str(uuid.uuid4())}}
        state = graph.invoke(
            {"target_id": "merck_tuvusertib", "retry_count": 0, "ux_retry_count": 0,
             "approval_status": "AWAITING_APPROVAL"}, cfg,
        )
        assert state["ux_retry_count"] == 1
        assert state["ux_lint_verdict"]["status"] == "PASS"  # clean on regeneration
        assert "on disk" not in state["draft_assets"]["sniper_email.md"]
        snap = graph.get_state(cfg)
        assert "dispatch_package" in snap.next


# ---------------------------------------------------------------------------
# 5. Full E2E: merck_tuvusertib pauses at the dispatch interrupt
# ---------------------------------------------------------------------------


class TestE2EMerckTuvusertib:
    def test_full_run_pause_and_approve(self, tmp_path, tmp_kit):
        graph = _fresh_graph(tmp_path)
        cfg = {"configurable": {"thread_id": "e2e-" + str(uuid.uuid4())}}
        state = graph.invoke(
            {"target_id": "merck_tuvusertib", "retry_count": 0, "ux_retry_count": 0,
             "approval_status": "AWAITING_APPROVAL"}, cfg,
        )

        # paused BEFORE dispatch_package
        snap = graph.get_state(cfg)
        assert snap.next == ("dispatch_package",)
        assert state["approval_status"] == "AWAITING_APPROVAL"
        assert state["anti_slop_verdict"]["status"] == "PASS"
        assert state["ux_lint_verdict"]["status"] == "PASS"

        # all artifacts exist on disk
        artifacts = state["built_artifacts"]["assets"]
        for name, path in artifacts.items():
            assert Path(path).exists(), f"{name} missing at {path}"
        manifest = json.loads(Path(artifacts["palmier_timeline_manifest"]).read_text())
        assert manifest["status"] in ("dry_run", "live", "unavailable")
        # Palmier leg is wired into the crispro-gtm-studio kit
        palm = manifest["palmier"]
        assert palm["kit"]["skill"] == "pharma-bd-sniper-reel"
        assert palm["kit"]["timeline_script"] == "timelines/video2_pharma_sniper.py"
        assert "mcp_client.py" in palm["kit"]["client"]
        if manifest["queue"]:
            # kit grammar: tools/call steps, 24 fps, one-target-one-render close
            tools = [q["tool"] for q in manifest["queue"]]
            assert tools[0] == "manage_project" and tools[-1] == "manage_project"
            assert "add_texts" in tools and "export_project" in tools

        # evidence packet provenance is honest
        packet = VerifiedEvidencePacket(**state["evidence_packet"])
        provenances = {v.provenance for v in packet.verified_values}
        assert "frozen_receipt" in provenances or "signed_artifact" in provenances
        assert "user_supplied_campaign_registry" in provenances

        # approve -> dispatch
        final = graph.invoke(None, cfg)
        assert final["approval_status"] == "DISPATCHED"
        out_dir = Path(list(artifacts.values())[0]).parent
        assert (out_dir / "dispatch_manifest.json").exists()

    def test_gtm_router_endpoints(self, tmp_path):
        """The drop-in router runs the same graph over HTTP semantics."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver

        from orchestration.crispro_gtm_graph.routers import gtm as gtm_router_mod

        app = FastAPI()
        app.include_router(gtm_router_mod.router)
        # fresh graph for hermetic test
        saver = SqliteSaver(sqlite3.connect(str(tmp_path / "ckpt.sqlite"), check_same_thread=False))
        gtm_router_mod._graph = gtm_graph.build_gtm_graph(checkpointer=saver)
        client = TestClient(app)

        r = client.post("/api/v1/gtm/campaigns/run", json={"target_id": "merck_tuvusertib"})
        assert r.status_code == 200
        body = r.json()
        assert body["paused_at_interrupt"] is True
        assert body["approval_status"] == "AWAITING_APPROVAL"
        thread_id = body["thread_id"]

        r2 = client.post(f"/api/v1/gtm/campaigns/{thread_id}/approve")
        assert r2.status_code == 200
        assert r2.json()["dispatched"] is True

        r3 = client.get(f"/api/v1/gtm/campaigns/{thread_id}/state")
        assert r3.status_code == 200
        assert r3.json()["values"]["approval_status"] == "DISPATCHED"

    def test_kb_router_search_endpoint(self, index_db):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from orchestration.crispro_gtm_graph.routers import kb_router

        db, _ = index_db
        app = FastAPI()
        app.include_router(kb_router.router)
        client = TestClient(app, raise_server_exceptions=False)

        r = client.get("/api/kb/aacr2026/search", params={"q": "ATR", "limit": 3})
        assert r.status_code == 200
        assert r.json()["total_hits"] > 0

        r2 = client.get("/api/kb/aacr2026/search", params={"q": "tuvusertib"})
        assert r2.status_code == 200
        assert r2.json()["total_hits"] == 0  # honest zero
