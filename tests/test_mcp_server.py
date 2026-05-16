"""
MCP server tests. Exercises:
  - All 8 tool handlers directly (correct DB writes/reads)
  - TOOLS schema integrity (every handler has a matching tool entry and vice versa)
  - JSON-RPC dispatch via a subprocess running the real server.py

Run: python -m pytest tests/test_mcp_server.py -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import state as state_module  # noqa: E402
from state import OpenEyeDB  # noqa: E402

import mcp_server  # noqa: E402


class TestHandlers(unittest.TestCase):
    """Each MCP tool handler returns JSON. We check the JSON shape and
    that the side effects (DB writes) actually happened."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def test_start_and_end_visual_session(self):
        result = json.loads(mcp_server.handle_start_visual_session({
            "device_type": "hololens",
            "procedure_id": "p-1",
            "procedure_name": "Test Proc",
        }))
        self.assertIn("visual_session_id", result)
        vsid = result["visual_session_id"]
        # Confirm it's persisted
        self.assertIsNotNone(self.db.get_visual_session(vsid))

        end_result = json.loads(mcp_server.handle_end_visual_session({
            "visual_session_id": vsid,
            "outcome": "completed",
        }))
        self.assertTrue(end_result["ok"])

    def test_log_frame(self):
        vsid = self.db.create_visual_session(device_type="webxr")
        result = json.loads(mcp_server.handle_log_frame({
            "visual_session_id": vsid,
            "sequence_num": 1,
            "scene_description": "operator inserting M6 bolt",
            "objects_detected": ["bolt", "hand"],
            "confidence": 0.92,
        }))
        self.assertIsInstance(result["frame_id"], int)

    def test_verify_step(self):
        vsid = self.db.create_visual_session(device_type="ios")
        result = json.loads(mcp_server.handle_verify_step({
            "visual_session_id": vsid,
            "step_id": "s1",
            "result": "pass",
            "confidence": 0.88,
            "reasoning": "All conditions met",
        }))
        self.assertIsInstance(result["verification_id"], int)

        # Confirm it counted
        vs = self.db.get_visual_session(vsid)
        self.assertEqual(vs["step_count"], 1)
        self.assertEqual(vs["steps_verified"], 1)

    def test_write_and_recall_skill(self):
        write_result = json.loads(mcp_server.handle_write_skill({
            "name": "mcp-test-skill",
            "content": "verifying procedure step three correctly",
            "description": "test skill via MCP",
            "domain": "test",
        }))
        self.assertIn("id", write_result)

        recall_result = json.loads(mcp_server.handle_recall_skill({
            "task": "verify procedure step",
            "top_k": 5,
        }))
        names = [s["name"] for s in recall_result]
        self.assertIn("mcp-test-skill", names)

    def test_search_memory(self):
        sid = self.db.create_session()
        self.db.append_message(sid, "user", "verify the bolt assembly step three")
        result = json.loads(mcp_server.handle_search_memory({
            "query": "bolt assembly",
            "limit": 10,
        }))
        self.assertGreater(len(result), 0)

    def test_search_frames(self):
        vsid = self.db.create_visual_session(device_type="test")
        self.db.log_frame(vsid, 1, "operator placing a widget into slot B")
        result = json.loads(mcp_server.handle_search_frames({
            "query": "widget slot",
            "limit": 10,
        }))
        self.assertGreater(len(result), 0)


class TestToolsRegistryIntegrity(unittest.TestCase):
    """The TOOLS list and HANDLERS dict must agree — every tool has a
    handler and every handler has a tool entry."""

    def test_every_tool_has_a_handler(self):
        for tool in mcp_server.TOOLS:
            self.assertIn(tool["name"], mcp_server.HANDLERS,
                          f"Tool {tool['name']} declared but no handler")

    def test_every_handler_has_a_tool(self):
        tool_names = {t["name"] for t in mcp_server.TOOLS}
        for name in mcp_server.HANDLERS:
            self.assertIn(name, tool_names,
                          f"Handler {name} exists but no tool entry")

    def test_exactly_eight_tools(self):
        self.assertEqual(len(mcp_server.TOOLS), 8)
        self.assertEqual(len(mcp_server.HANDLERS), 8)

    def test_tools_have_input_schemas(self):
        for tool in mcp_server.TOOLS:
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("required", tool["inputSchema"])

    def test_verify_step_enum_locks_results(self):
        verify = next(t for t in mcp_server.TOOLS if t["name"] == "verify_step")
        result_schema = verify["inputSchema"]["properties"]["result"]
        self.assertEqual(set(result_schema["enum"]), {"pass", "fail", "uncertain"})


class TestJSONRPCDispatch(unittest.TestCase):
    """Spawn the MCP server as a subprocess and send real JSON-RPC over stdio."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_home = tempfile.mkdtemp(prefix="openeye-mcp-")
        env = os.environ.copy()
        env["OPENEYE_HOME"] = cls.tmp_home
        # Prevent context-sync banner from polluting stderr
        env["OPENEYE_CONTEXT_OPTIN"] = "false"
        cls.proc = subprocess.Popen(
            [sys.executable, "sidecar/mcp_server.py"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=os.path.join(os.path.dirname(__file__), ".."),
            env=env, text=True, bufsize=1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close()
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        import shutil
        shutil.rmtree(cls.tmp_home, ignore_errors=True)

    def _rpc(self, method, params=None, msg_id=1):
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def test_initialize(self):
        r = self._rpc("initialize", msg_id=1)
        self.assertEqual(r["id"], 1)
        self.assertEqual(r["result"]["serverInfo"]["name"], "openeye")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list(self):
        r = self._rpc("tools/list", msg_id=2)
        tools = r["result"]["tools"]
        self.assertEqual(len(tools), 8)
        names = {t["name"] for t in tools}
        self.assertIn("verify_step", names)
        self.assertIn("log_frame", names)

    def test_tools_call_start_visual_session(self):
        r = self._rpc("tools/call", params={
            "name": "start_visual_session",
            "arguments": {"device_type": "hololens"},
        }, msg_id=3)
        text = r["result"]["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("visual_session_id", data)

    def test_tools_call_unknown_tool_returns_error(self):
        r = self._rpc("tools/call", params={
            "name": "nonexistent_tool", "arguments": {},
        }, msg_id=4)
        self.assertIn("error", r)
        self.assertEqual(r["error"]["code"], -32601)

    def test_unknown_method_returns_error(self):
        r = self._rpc("definitely/not/a/method", msg_id=5)
        self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()
