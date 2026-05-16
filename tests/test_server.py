"""
OpenEye sidecar HTTP integration tests.
Exercises every FastAPI route end-to-end via TestClient against an isolated
temp SQLite database.

Run: python -m pytest tests/test_server.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Point OPENEYE_HOME at a temp dir BEFORE importing anything that touches state
_TMP_HOME = tempfile.mkdtemp(prefix="openeye-test-")
os.environ["OPENEYE_HOME"] = _TMP_HOME

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import state as state_module  # noqa: E402
from state import OpenEyeDB  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server import app  # noqa: E402


class TestServerEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Force a fresh DB owned by this test class so prior tests
        # that closed the singleton can't bleed in
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.tmp.close()
        cls.db = OpenEyeDB(Path(cls.tmp.name))
        state_module._db_instance = cls.db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        state_module._db_instance = None
        try:
            os.unlink(cls.tmp.name)
        except OSError:
            pass

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIn("db", body)
        self.assertIn("x-request-id", r.headers)

    def test_request_id_echo(self):
        r = self.client.get("/health", headers={"x-request-id": "my-trace-123"})
        self.assertEqual(r.headers["x-request-id"], "my-trace-123")

    def test_session_lifecycle(self):
        r = self.client.post("/sessions/create",
                             json={"source": "test", "tenant_id": "t-http", "model": "m1"})
        self.assertEqual(r.status_code, 200)
        sid = r.json()["session_id"]
        self.assertEqual(len(sid), 36)

        r = self.client.post(f"/sessions/{sid}/messages",
                             json={"role": "user", "content": "hello"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("message_id", r.json())

        r = self.client.post(f"/sessions/{sid}/messages",
                             json={"role": "assistant", "content": "hi there"})
        self.assertEqual(r.status_code, 200)

        r = self.client.get(f"/sessions/{sid}/messages")
        self.assertEqual(r.status_code, 200)
        msgs = r.json()["messages"]
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")

        r = self.client.post(f"/sessions/{sid}/end", json={"reason": "completed"})
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/sessions", params={"tenant_id": "t-http"})
        self.assertEqual(r.status_code, 200)
        sessions = r.json()["sessions"]
        self.assertGreaterEqual(len(sessions), 1)

    def test_search_messages(self):
        r = self.client.post("/sessions/create", json={"source": "search-test"})
        sid = r.json()["session_id"]
        self.client.post(f"/sessions/{sid}/messages",
                         json={"role": "user", "content": "sterile field compliance check"})
        r = self.client.post("/search/messages",
                             json={"query": "sterile field", "limit": 5})
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertGreater(len(results), 0)

    def test_visual_session_and_frames(self):
        r = self.client.post("/visual-sessions/create",
                             json={"device_type": "hololens", "procedure_id": "p1",
                                   "procedure_name": "Test Proc"})
        self.assertEqual(r.status_code, 200)
        vsid = r.json()["visual_session_id"]

        r = self.client.get(f"/visual-sessions/{vsid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["device_type"], "hololens")

        # 404 returns structured error body
        r = self.client.get("/visual-sessions/does-not-exist")
        self.assertEqual(r.status_code, 404)
        body = r.json()
        self.assertEqual(body["error"], "not_found")
        self.assertIn("request_id", body)

        # Log frames
        r = self.client.post("/frames/log",
                             json={"visual_session_id": vsid, "sequence_num": 1,
                                   "scene_description": "operator placing widget A in slot B",
                                   "objects_detected": ["widget-a", "slot-b"],
                                   "confidence": 0.9})
        self.assertEqual(r.status_code, 200)
        fid = r.json()["frame_id"]
        self.assertIsInstance(fid, int)

        # Search frames
        r = self.client.post("/search/frames", json={"query": "widget"})
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()["results"]), 0)

        # End visual session
        r = self.client.post(f"/visual-sessions/{vsid}/end",
                             json={"outcome": "completed"})
        self.assertEqual(r.status_code, 200)

    def test_step_verification(self):
        vsid = self.client.post("/visual-sessions/create",
                                json={"device_type": "ios"}).json()["visual_session_id"]

        r = self.client.post("/steps/log",
                             json={"visual_session_id": vsid, "step_id": "s1",
                                   "result": "pass", "confidence": 0.95})
        self.assertEqual(r.status_code, 200)
        self.assertIn("verification_id", r.json())

        # Invalid result -> 422 with structured error body
        r = self.client.post("/steps/log",
                             json={"visual_session_id": vsid, "step_id": "s2",
                                   "result": "maybe"})
        self.assertEqual(r.status_code, 422)
        body = r.json()
        self.assertEqual(body["error"], "validation_error")

    def test_skills_lifecycle(self):
        r = self.client.post("/skills/write",
                             json={"name": "http-test-skill",
                                   "content": "how to verify HTTP integration tests",
                                   "description": "test skill", "domain": "test"})
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/skills", params={"domain": "test"})
        self.assertEqual(r.status_code, 200)
        names = [s["name"] for s in r.json()["skills"]]
        self.assertIn("http-test-skill", names)

        r = self.client.post("/skills/recall",
                             json={"task": "verify HTTP integration", "top_k": 3})
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()["skills"]), 0)

        r = self.client.post("/skills/context",
                             json={"task": "verify HTTP integration"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("http-test-skill", r.json()["context"])

    def test_trajectory_capture_and_export(self):
        sid = self.client.post("/sessions/create",
                               json={"source": "traj-test"}).json()["session_id"]
        self.client.post(f"/sessions/{sid}/messages",
                         json={"role": "user", "content": "step 1?"})
        self.client.post(f"/sessions/{sid}/messages",
                         json={"role": "assistant", "content": "step 1 complete"})

        vsid = self.client.post("/visual-sessions/create",
                                json={"device_type": "webxr"}).json()["visual_session_id"]
        self.client.post("/steps/log",
                         json={"visual_session_id": vsid, "step_id": "s1", "result": "pass"})
        self.client.post("/steps/log",
                         json={"visual_session_id": vsid, "step_id": "s2", "result": "pass"})

        r = self.client.post("/trajectories/capture",
                             json={"session_id": sid, "completed": True, "model": "test-m",
                                   "visual_session_id": vsid, "tags": ["test-proc"]})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["trajectory_id"])

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            out_path = f.name
        try:
            r = self.client.post("/trajectories/export",
                                 json={"output_path": out_path, "completed_only": True})
            self.assertEqual(r.status_code, 200)
            self.assertGreaterEqual(r.json()["exported"], 1)
        finally:
            os.unlink(out_path)

    def test_dpo_export_endpoint(self):
        # Seed two trajectories on same procedure with high+low reward
        sid_a = self.client.post("/sessions/create", json={}).json()["session_id"]
        self.client.post(f"/sessions/{sid_a}/messages",
                         json={"role": "user", "content": "go"})
        self.client.post(f"/sessions/{sid_a}/messages",
                         json={"role": "assistant", "content": "did it well"})

        vsid_a = self.client.post("/visual-sessions/create",
                                  json={"device_type": "test"}).json()["visual_session_id"]
        for _ in range(5):
            self.client.post("/steps/log",
                             json={"visual_session_id": vsid_a, "step_id": "s", "result": "pass"})
        self.client.post("/trajectories/capture",
                         json={"session_id": sid_a, "completed": True, "model": "m1",
                               "visual_session_id": vsid_a, "tags": ["dpo-test"]})

        sid_b = self.client.post("/sessions/create", json={}).json()["session_id"]
        self.client.post(f"/sessions/{sid_b}/messages",
                         json={"role": "user", "content": "go"})
        self.client.post(f"/sessions/{sid_b}/messages",
                         json={"role": "assistant", "content": "did it poorly"})
        vsid_b = self.client.post("/visual-sessions/create",
                                  json={"device_type": "test"}).json()["visual_session_id"]
        for _ in range(5):
            self.client.post("/steps/log",
                             json={"visual_session_id": vsid_b, "step_id": "s", "result": "fail"})
        self.client.post("/trajectories/capture",
                         json={"session_id": sid_b, "completed": True, "model": "m1",
                               "visual_session_id": vsid_b, "tags": ["dpo-test"]})

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            out_path = f.name
        try:
            r = self.client.post("/trajectories/export-dpo",
                                 json={"output_path": out_path,
                                       "chosen_threshold": 0.8,
                                       "rejected_threshold": 0.4})
            self.assertEqual(r.status_code, 200)
            self.assertGreaterEqual(r.json()["exported"], 1)
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_sync_now_endpoint(self):
        # /sync/now returns a dict of per-table counts.
        # Exact contents depend on whether OPENEYE_CLOUD_URL was set at import.
        r = self.client.post("/sync/now")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("synced", body)
        self.assertIsInstance(body["synced"], dict)

    def test_404_structured_error(self):
        r = self.client.get("/does/not/exist")
        self.assertEqual(r.status_code, 404)
        # Default FastAPI 404 still goes through our handler shape
        body = r.json()
        self.assertIn("detail", body)


class TestSidecarAuth(unittest.TestCase):
    """When OPENEYE_SIDECAR_TOKEN is set, requests must carry the bearer
    header — except /health which the spawner needs unauthenticated."""

    @classmethod
    def setUpClass(cls):
        # Bring up a fresh app instance with the auth env var set.
        # We reload the server module so the SIDECAR_TOKEN constant updates.
        os.environ["OPENEYE_SIDECAR_TOKEN"] = "secret-test-token"
        import importlib
        import server as srv
        importlib.reload(srv)
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        cls.tmp.close()
        cls.db = OpenEyeDB(Path(cls.tmp.name))
        state_module._db_instance = cls.db
        cls.client = TestClient(srv.app)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        state_module._db_instance = None
        try:
            os.unlink(cls.tmp.name)
        except OSError:
            pass
        del os.environ["OPENEYE_SIDECAR_TOKEN"]
        # Re-reload server to clear the token for other test files
        import importlib
        import server
        importlib.reload(server)

    def test_health_does_not_require_auth(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)

    def test_missing_auth_returns_401(self):
        r = self.client.post("/sessions/create", json={"source": "test"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "unauthorized")

    def test_wrong_token_returns_401(self):
        r = self.client.post("/sessions/create", json={"source": "test"},
                             headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(r.status_code, 401)

    def test_correct_token_passes(self):
        r = self.client.post("/sessions/create", json={"source": "test"},
                             headers={"Authorization": "Bearer secret-test-token"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
