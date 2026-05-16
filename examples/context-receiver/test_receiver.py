"""
Contract conformance tests for the reference Context receiver.

These tests exercise the receiver against the contract documented in
docs/context-data.md. They are the same checks OpenEye's context_sync
worker assumes about the endpoint, so passing them means the receiver
is wire-compatible.

Run: python -m pytest test_receiver.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["CONTEXT_RECEIVER_TOKENS"] = "ctx-test-key:tenant-a,ctx-other-key:tenant-b"
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["CONTEXT_RECEIVER_DB"] = _TMP.name

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
import server as srv  # noqa: E402

# Force a fresh store per test module
srv._store = None
srv.DB_PATH = Path(_TMP.name)


def make_trajectory(tid: str, proc: str = "bolt-assembly", reward: float = 0.9):
    return {
        "trajectory_id": tid,
        "schema_version": "1.0",
        "model": "claude-sonnet-4-6",
        "completed": True,
        "reward_signal": reward,
        "procedure_tag": proc,
        "conversations": [
            {"from": "human", "value": "frame description"},
            {"from": "gpt", "value": "verify_step pass"},
        ],
        "created_at": 1715000000.0,
    }


def make_batch(batch_id: str, trajectories):
    return {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "trajectory_count": len(trajectories),
        "trajectories": trajectories,
    }


class TestReceiverContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(srv.app)
        cls.auth_a = {"Authorization": "Bearer ctx-test-key"}
        cls.auth_b = {"Authorization": "Bearer ctx-other-key"}

    # ── Health ──────────────────────────────────────────────────────────────

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    # ── Auth ────────────────────────────────────────────────────────────────

    def test_missing_auth_returns_401(self):
        r = self.client.post("/v1/openeye",
                             json=make_batch("b-noauth", [make_trajectory("t-1")]))
        self.assertEqual(r.status_code, 401)

    def test_bad_token_returns_401(self):
        r = self.client.post("/v1/openeye",
                             headers={"Authorization": "Bearer wrong-token"},
                             json=make_batch("b-badtok", [make_trajectory("t-2")]))
        self.assertEqual(r.status_code, 401)

    def test_malformed_auth_header_returns_401(self):
        r = self.client.post("/v1/openeye",
                             headers={"Authorization": "ctx-test-key"},  # missing Bearer
                             json=make_batch("b-malformed", [make_trajectory("t-3")]))
        self.assertEqual(r.status_code, 401)

    # ── Happy path ──────────────────────────────────────────────────────────

    def test_ingest_success(self):
        r = self.client.post("/v1/openeye", headers=self.auth_a,
                             json=make_batch("b-ok-1", [make_trajectory("t-ok-1")]))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["received"], 1)
        self.assertFalse(body["duplicate"])

    def test_ingest_with_batch_id_header(self):
        batch_id = "b-with-header"
        r = self.client.post(
            "/v1/openeye",
            headers={**self.auth_a, "X-OpenEye-Batch-Id": batch_id,
                     "X-OpenEye-Schema": "1.0", "X-OpenEye-Client": "sidecar/1.0"},
            json=make_batch(batch_id, [make_trajectory("t-with-header")]))
        self.assertEqual(r.status_code, 200)

    # ── Idempotency ─────────────────────────────────────────────────────────

    def test_replay_same_batch_returns_duplicate(self):
        batch = make_batch("b-replay", [make_trajectory("t-replay-1")])
        r1 = self.client.post("/v1/openeye", headers=self.auth_a, json=batch)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["received"], 1)

        r2 = self.client.post("/v1/openeye", headers=self.auth_a, json=batch)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["duplicate"])
        self.assertEqual(r2.json()["received"], 0)

    def test_batch_id_header_mismatch_rejected(self):
        r = self.client.post(
            "/v1/openeye",
            headers={**self.auth_a, "X-OpenEye-Batch-Id": "header-says-this"},
            json=make_batch("body-says-that", [make_trajectory("t-mm")]))
        self.assertEqual(r.status_code, 400)

    # ── Schema ──────────────────────────────────────────────────────────────

    def test_unsupported_schema_rejected(self):
        batch = make_batch("b-badschema", [make_trajectory("t-bs")])
        batch["schema_version"] = "99.9"
        r = self.client.post("/v1/openeye", headers=self.auth_a, json=batch)
        self.assertEqual(r.status_code, 400)

    def test_schema_header_mismatch_rejected(self):
        # Body schema matches, but the X-OpenEye-Schema header advertises
        # a different version — must reject. Catches a future-sidecar
        # contract drift where the header gets bumped before the body.
        r = self.client.post(
            "/v1/openeye",
            headers={**self.auth_a, "X-OpenEye-Schema": "2.0"},
            json=make_batch("b-hdrschema", [make_trajectory("t-hs")]))
        self.assertEqual(r.status_code, 400)

    def test_malformed_body_returns_422(self):
        r = self.client.post("/v1/openeye", headers=self.auth_a,
                             json={"wrong": "shape"})
        self.assertEqual(r.status_code, 422)

    # ── Tenant isolation ────────────────────────────────────────────────────

    def test_tenant_a_cannot_see_tenant_b_data(self):
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-tenant-a", [make_trajectory("t-tenant-a")]))
        self.client.post("/v1/openeye", headers=self.auth_b,
                         json=make_batch("b-tenant-b", [make_trajectory("t-tenant-b")]))

        # Tenant A cannot fetch tenant B's trajectory
        r = self.client.get("/v1/openeye/trajectories/t-tenant-b", headers=self.auth_a)
        self.assertEqual(r.status_code, 404)

        # Tenant B sees their own
        r = self.client.get("/v1/openeye/trajectories/t-tenant-b", headers=self.auth_b)
        self.assertEqual(r.status_code, 200)

    # ── List & filter ───────────────────────────────────────────────────────

    def test_list_trajectories(self):
        # Seed two procedures
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-list-1",
                                         [make_trajectory("t-list-1", proc="proc-x")]))
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-list-2",
                                         [make_trajectory("t-list-2", proc="proc-y")]))

        r = self.client.get("/v1/openeye/trajectories",
                            params={"procedure_tag": "proc-x"}, headers=self.auth_a)
        self.assertEqual(r.status_code, 200)
        ids = [t["trajectory_id"] for t in r.json()["trajectories"]]
        self.assertIn("t-list-1", ids)
        self.assertNotIn("t-list-2", ids)

    # ── DSAR / delete ───────────────────────────────────────────────────────

    def test_soft_delete(self):
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-del", [make_trajectory("t-del")]))

        r = self.client.delete("/v1/openeye/trajectories/t-del", headers=self.auth_a)
        self.assertEqual(r.status_code, 200)

        # Now invisible
        r = self.client.get("/v1/openeye/trajectories/t-del", headers=self.auth_a)
        self.assertEqual(r.status_code, 404)

    def test_soft_deleted_not_in_list(self):
        # The single-fetch path was tested above; this asserts the list
        # endpoint also respects deleted_at IS NULL so DSAR'd rows
        # disappear from operator/tenant browsing too.
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-list-del",
                                         [make_trajectory("t-list-del-keep", proc="proc-keep"),
                                          make_trajectory("t-list-del-drop", proc="proc-drop")]))

        r = self.client.delete("/v1/openeye/trajectories/t-list-del-drop",
                               headers=self.auth_a)
        self.assertEqual(r.status_code, 200)

        r = self.client.get("/v1/openeye/trajectories", headers=self.auth_a)
        self.assertEqual(r.status_code, 200)
        ids = [t["trajectory_id"] for t in r.json()["trajectories"]]
        self.assertIn("t-list-del-keep", ids)
        self.assertNotIn("t-list-del-drop", ids)

    def test_delete_unknown_returns_404(self):
        r = self.client.delete("/v1/openeye/trajectories/does-not-exist",
                               headers=self.auth_a)
        self.assertEqual(r.status_code, 404)

    def test_delete_other_tenants_data_returns_404(self):
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-cross", [make_trajectory("t-cross")]))
        # Tenant B should not be able to delete tenant A's trajectory
        r = self.client.delete("/v1/openeye/trajectories/t-cross", headers=self.auth_b)
        self.assertEqual(r.status_code, 404)

    # ── Batches ─────────────────────────────────────────────────────────────

    def test_list_batches(self):
        self.client.post("/v1/openeye", headers=self.auth_a,
                         json=make_batch("b-listbatch-1", [make_trajectory("t-lb-1")]))
        r = self.client.get("/v1/openeye/batches", headers=self.auth_a)
        self.assertEqual(r.status_code, 200)
        batch_ids = [b["batch_id"] for b in r.json()["batches"]]
        self.assertIn("b-listbatch-1", batch_ids)


if __name__ == "__main__":
    unittest.main()
