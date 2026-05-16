"""
Retention tests — prune_older_than correctly deletes old rows, skips
protected categories (skills, tenant opt-in, Context-synced trajectories),
and respects the disable switch.

Run: python -m pytest tests/test_retention.py -v
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import state as state_module  # noqa: E402
from state import OpenEyeDB  # noqa: E402


class TestPruneOlderThan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db
        self.now = time.time()
        self.day_ago = self.now - 86400
        self.week_ago = self.now - 7 * 86400

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def _backdate_session(self, sid: str, when: float):
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE sessions SET started_at=? WHERE id=?", (when, sid))
            self.db._conn.commit()

    def _backdate_visual_session(self, vsid: str, when: float):
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE visual_sessions SET started_at=? WHERE id=?", (when, vsid))
            self.db._conn.execute(
                "UPDATE frames SET captured_at=? WHERE visual_session_id=?",
                (when, vsid))
            self.db._conn.execute(
                "UPDATE step_verifications SET verified_at=? WHERE visual_session_id=?",
                (when, vsid))
            self.db._conn.commit()

    def _backdate_trajectory(self, tid: str, when: float):
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE trajectories SET created_at=? WHERE id=?", (when, tid))
            self.db._conn.commit()

    def test_prune_old_session(self):
        old_sid = self.db.create_session(source="old")
        self.db.append_message(old_sid, "user", "old data")
        self._backdate_session(old_sid, self.week_ago)

        fresh_sid = self.db.create_session(source="fresh")
        self.db.append_message(fresh_sid, "user", "fresh data")

        # Cutoff: 3 days ago — should delete the week-old session
        cutoff = self.now - 3 * 86400
        deleted = self.db.prune_older_than(cutoff)

        self.assertEqual(deleted["sessions"], 1)
        self.assertGreaterEqual(deleted["messages"], 1)

        remaining = self.db.list_sessions()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["source"], "fresh")

    def test_prune_old_visual_session_cascade(self):
        old_vsid = self.db.create_visual_session(device_type="old")
        self.db.log_frame(old_vsid, 1, "old frame")
        self.db.log_step_verification(old_vsid, "s1", "pass")
        self._backdate_visual_session(old_vsid, self.week_ago)

        new_vsid = self.db.create_visual_session(device_type="new")
        self.db.log_frame(new_vsid, 1, "new frame")

        cutoff = self.now - 3 * 86400
        deleted = self.db.prune_older_than(cutoff)

        self.assertEqual(deleted["visual_sessions"], 1)
        self.assertEqual(deleted["frames"], 1)
        self.assertEqual(deleted["step_verifications"], 1)

        self.assertIsNone(self.db.get_visual_session(old_vsid))
        self.assertIsNotNone(self.db.get_visual_session(new_vsid))

    def test_synced_trajectory_protected_from_prune(self):
        synced_id = self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"])
        unsynced_id = self.db.save_trajectory(
            conversations=[{"from": "human", "value": "y"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"])
        self._backdate_trajectory(synced_id, self.week_ago)
        self._backdate_trajectory(unsynced_id, self.week_ago)

        # Mark one as already shipped to Context
        self.db.mark_sent_to_context([synced_id], batch_id="b1")

        cutoff = self.now - 3 * 86400
        deleted = self.db.prune_older_than(cutoff)

        # Unsynced gets deleted, synced is protected
        self.assertEqual(deleted["trajectories"], 1)
        # Verify
        with self.db._lock:
            survivors = self.db._conn.execute(
                "SELECT id FROM trajectories").fetchall()
        ids = [r["id"] for r in survivors]
        self.assertIn(synced_id, ids)
        self.assertNotIn(unsynced_id, ids)

    def test_skills_never_pruned(self):
        self.db.upsert_skill("ancient-skill", "old content")
        # Backdate the skill
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE skills SET created_at=? WHERE name=?",
                (self.week_ago, "ancient-skill"))
            self.db._conn.commit()

        self.db.prune_older_than(self.now - 3 * 86400)
        # Still here
        skill = self.db.get_skill("ancient-skill")
        self.assertIsNotNone(skill)

    def test_tenant_optin_never_pruned(self):
        self.db.set_tenant_optin("acme", True)
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE tenant_context_optin SET updated_at=? WHERE tenant_id=?",
                (self.week_ago, "acme"))
            self.db._conn.commit()

        self.db.prune_older_than(self.now - 3 * 86400)
        # Still here
        self.assertTrue(self.db.is_tenant_opted_in("acme"))

    def test_no_data_to_prune_returns_zeros(self):
        deleted = self.db.prune_older_than(self.now - 86400)
        self.assertEqual(sum(deleted.values()), 0)


class TestRetentionWorker(unittest.TestCase):
    """The worker is enable-gated. We test the public API without spinning
    the actual thread (which would sleep)."""

    def setUp(self):
        # Force-enable for these tests
        os.environ["OPENEYE_RETAIN_DAYS"] = "30"
        import importlib
        import retention
        importlib.reload(retention)
        self.retention = retention

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)
        del os.environ["OPENEYE_RETAIN_DAYS"]

    def test_is_enabled_true(self):
        self.assertTrue(self.retention.is_enabled())

    def test_prune_now_returns_summary(self):
        result = self.retention.prune_now()
        self.assertTrue(result["enabled"])
        self.assertIn("cutoff_epoch", result)
        self.assertIn("deleted", result)

    def test_disabled_returns_no_op(self):
        with mock.patch.object(self.retention, "RETAIN_DAYS", 0):
            result = self.retention.prune_now()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["deleted"], {})


if __name__ == "__main__":
    unittest.main()
