"""Palmier bridge integration tests.

Proves the CrisPRO-GTM-Graph is wired into the crispro-gtm-studio kit:
  1. The bridge loads the kit's OWN PalmierMCP client (scripts/mcp_client.py).
  2. The queue mirrors the kit's timeline grammar (tools/call, 24 fps,
     6-beat frames 0/72/192/264/408/504, accent color, one-target-one-render).
  3. A stub streamable-HTTP MCP server (same protocol as Palmier Pro's
     MCPHTTPServer.swift) executes the queue LIVE through the kit client.
  4. The SKILL.md card gate blocks live render when the target card is
     missing and writes an honest draft skeleton instead.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestration.crispro_gtm_graph import config, palmier_bridge

# tmp_kit fixture lives in conftest.py

VO_SCRIPT = """[00:00] Every pharma deck in this class looks the same.
[00:05] tuvusertib: responder biology is the interesting question.
[00:55] Research use only. Book the demo.
"""

CURATED_CARD = {
    "company": "Merck KGaA / EMD Serono",
    "salutation": "Merck KGaA team",
    "trial": {
        "name": "DDRiver EOC 302",
        "nct": "NCT05950464",
        "regimen": "Tuvusertib (M1774, ATRi)",
        "failure_mode": "Biomarker stratum underpowered",
        "responder_evidence": "MBD4-loss rescue view",
    },
    "rescue_view": "captures/mss_crc_cart.mp4",
    "proof_figure": "figures/merck_300dpi.png",
    "responder_stratum": "MBD4-loss, pre-treatment DNA",
}


# ---------------------------------------------------------------------------
# Stub MCP server (streamable HTTP, Palmier Pro protocol shape)
# ---------------------------------------------------------------------------


class StubPalmierServer:
    """Speaks the subset of MCPHTTPServer.swift the kit client uses:
    initialize -> Mcp-Session-Id header; tools/call -> JSON-RPC result;
    DELETE -> session teardown."""

    def __init__(self):
        self.app = FastAPI()
        self.calls: list = []
        self.session_ids: set = set()
        self._setup()

    def _setup(self):
        @self.app.post("/mcp")
        async def mcp(request: Request):
            body = await request.json()
            method = body.get("method")
            sid = request.headers.get("Mcp-Session-Id")
            if method == "initialize":
                new_sid = f"stub-{uuid.uuid4().hex[:12]}"
                self.session_ids.add(new_sid)
                self.calls.append({"method": method, "session": new_sid})
                return _jsonrpc(body["id"], {"protocolVersion": "2025-03-26", "capabilities": {}}, sid=new_sid)
            if method == "notifications/initialized":
                self.calls.append({"method": method, "session": sid})
                return {}
            if method == "tools/call":
                if sid not in self.session_ids:
                    return _jsonrpc(body["id"], None, err="session expired")
                name = body["params"]["name"]
                args = body["params"].get("arguments", {})
                self.calls.append({"method": "tools/call", "tool": name, "args": args, "session": sid})
                if name == "export_project":
                    result = {"jobId": "stub-job-1", "status": "queued"}
                elif name == "manage_exports":
                    result = [{"jobId": "stub-job-1", "status": "completed", "path": "/tmp/stub.mp4"}]
                else:
                    result = {"ok": True}
                return _jsonrpc(body["id"], {"content": [{"type": "text", "text": json.dumps(result)}]})
            return _jsonrpc(body.get("id"), None, err=f"unsupported method {method}")

        @self.app.delete("/mcp")
        async def teardown(request: Request):
            sid = request.headers.get("Mcp-Session-Id")
            self.session_ids.discard(sid)
            self.calls.append({"method": "DELETE", "session": sid})
            return {"ok": True}

    def start(self) -> str:
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self._config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="critical")
        self._server = uvicorn.Server(self._config)
        thread = threading.Thread(target=self._server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if self._server.started:
                break
            time.sleep(0.05)
        assert self._server.started, "stub MCP server failed to start"
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self.url


def _jsonrpc(id_, result, sid=None, err=None):
    from fastapi.responses import JSONResponse

    body = {"jsonrpc": "2.0", "id": id_}
    if err:
        body["error"] = {"code": -32000, "message": err}
    else:
        body["result"] = result
    headers = {"Mcp-Session-Id": sid} if sid else {}
    return JSONResponse(body, headers=headers)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_server():
    server = StubPalmierServer()
    url = server.start()
    yield server, url
    server._server.should_exit = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKitWiring:
    def test_bridge_loads_real_kit_client(self):
        """The bridge imports PalmierMCP from the REAL kit's mcp_client.py."""
        PalmierMCP = palmier_bridge.load_palmier_client()
        assert PalmierMCP.__name__ == "PalmierMCP"
        assert hasattr(PalmierMCP, "call") and hasattr(PalmierMCP, "list_tools")

    def test_kit_map_points_at_real_kit_assets(self):
        """Every mapped skill + timeline script exists in the real kit."""
        kit = palmier_bridge.kit_dir()
        for target_id, mapping in palmier_bridge.KIT_MAP.items():
            assert (kit / "timelines" / mapping["script"]).exists(), f"{mapping['script']} missing"
            assert (kit / "skills" / mapping["skill"] / "SKILL.md").exists(), f"skill {mapping['skill']} missing"

    def test_queue_grammar_matches_kit(self, tmp_kit):
        queue = palmier_bridge.build_queue("merck_tuvusertib", VO_SCRIPT, CURATED_CARD)
        steps = [q["step"] for q in queue]
        # kit execution rules (SKILL.md): create -> timeline -> ... -> close
        assert steps[0] == "manage_project create"
        assert steps[-1].startswith("manage_project close")
        create = queue[0]["args"]
        assert create["name"] == "sniper-merck_kgaa-60s"
        assert create["fps"] == 24 and create["aspectRatio"] == "9:16"
        texts = next(q for q in queue if q["tool"] == "add_texts")["args"]["entries"]
        assert [t["atFrame"] for t in texts] == [0, 72, 192, 264, 408, 504]  # kit 6-beat map
        assert texts[0]["text"].startswith("DDRiver EOC 302")
        assert texts[3]["style"]["color"] == palmier_bridge.ACCENT  # kit accent
        captions = next(q for q in queue if q["tool"] == "add_captions")["args"]
        assert captions["maxWords"] == 4 and captions["highlightColor"] == palmier_bridge.ACCENT
        export = next(q for q in queue if q["tool"] == "export_project")["args"]
        assert export == {"mode": "video", "codec": "h264", "quality": "high"}


class TestLiveExecutionThroughKitClient:
    def test_live_run_uses_kit_client_against_stub_server(self, tmp_kit, stub_server, monkeypatch):
        server, url = stub_server
        monkeypatch.setattr(config, "PALMIER_MCP_URL", url)
        # curated card present -> live path
        (tmp_kit / "targets" / "merck_kgaa.json").write_text(json.dumps(CURATED_CARD))

        result = palmier_bridge.run_palmier_package("merck_tuvusertib", VO_SCRIPT)

        assert result["status"] == "live", result.get("reason")
        assert "mcp_client.py" in result["client"]
        tools_called = [c["tool"] for c in server.calls if c.get("method") == "tools/call"]
        # kit execution order: create, timeline, imports, clips, texts, captions, export, exports, close
        assert tools_called[0] == "manage_project"
        assert "add_texts" in tools_called and "add_captions" in tools_called
        assert "export_project" in tools_called and "manage_exports" in tools_called
        assert tools_called[-1] == "manage_project"  # close (one target, one render)
        # every tools/call carried a real session id issued by initialize
        sessions = {c["session"] for c in server.calls if c.get("method") == "tools/call"}
        assert sessions and all(s and s.startswith("stub-") for s in sessions)
        # session torn down on close
        assert any(c["method"] == "DELETE" for c in server.calls)

    def test_card_gate_blocks_live_without_card(self, tmp_kit, stub_server, monkeypatch):
        """SKILL.md gate: missing card -> dry_run + draft skeleton, never an
        improvised trial narrative."""
        server, url = stub_server
        monkeypatch.setattr(config, "PALMIER_MCP_URL", url)
        # NO card written

        result = palmier_bridge.run_palmier_package("merck_tuvusertib", VO_SCRIPT)

        assert result["status"] == "dry_run"
        assert "target card missing" in result["reason"]
        assert result["card"]["present"] is False
        draft = Path(result["card"]["draft"])
        assert draft.exists()
        draft_blob = json.loads(draft.read_text())
        assert draft_blob["_status"].startswith("DRAFT")
        assert draft_blob["trial"]["nct"] is None  # no invented NCT
        # queue marks card-dependent fields honestly
        requires = [q for q in result["queue"] if q.get("requires_card")]
        assert requires, "card-dependent steps must be flagged"
        texts = next(q for q in result["queue"] if q["tool"] == "add_texts")["args"]["entries"]
        assert any("REQUIRES_CARD" in t["text"] for t in texts)
        # nothing was sent to the server
        assert not [c for c in server.calls if c.get("method") == "tools/call"]

    def test_draft_card_contains_only_registry_identifiers(self, tmp_kit):
        palmier_bridge.write_draft_card("merck_tuvusertib")
        draft = json.loads((tmp_kit / "targets" / "merck_kgaa.draft.json").read_text())
        # only user-supplied registry identifiers; everything else null
        assert draft["trial"]["name"] == "DDRiver EOC 302"
        assert draft["trial"]["failure_mode"] is None
        assert draft["responder_stratum"] is None
