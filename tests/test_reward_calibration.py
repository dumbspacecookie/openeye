"""
Per-procedure reward calibration tests. Custom weights override the
default (1.0, 0.5, 0.0) formula. Tests cover the DB helpers and the
compute_visual_reward integration.

Run: python -m pytest tests/test_reward_calibration.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import state as state_module  # noqa: E402
from state import OpenEyeDB  # noqa: E402
import trajectories as traj_mod  # noqa: E402


class TestRewardWeightCRUD(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def test_default_when_unset(self):
        w = self.db.get_procedure_reward_weights("never-configured")
        self.assertEqual(w["pass_weight"], 1.0)
        self.assertEqual(w["uncertain_weight"], 0.5)
        self.assertEqual(w["fail_weight"], 0.0)

    def test_default_when_no_procedure_tag(self):
        w = self.db.get_procedure_reward_weights(None)
        self.assertEqual(w["pass_weight"], 1.0)
        self.assertEqual(w["uncertain_weight"], 0.5)

    def test_set_and_get(self):
        self.db.set_procedure_reward_weights(
            "surgical-prep",
            pass_weight=1.0, uncertain_weight=0.2, fail_weight=0.0,
            note="Sterile field — uncertainty is closer to fail")
        w = self.db.get_procedure_reward_weights("surgical-prep")
        self.assertEqual(w["uncertain_weight"], 0.2)
        self.assertEqual(w["note"], "Sterile field — uncertainty is closer to fail")

    def test_upsert_overwrites(self):
        self.db.set_procedure_reward_weights("p", uncertain_weight=0.3)
        self.db.set_procedure_reward_weights("p", uncertain_weight=0.7)
        w = self.db.get_procedure_reward_weights("p")
        self.assertEqual(w["uncertain_weight"], 0.7)

    def test_list_configs(self):
        self.db.set_procedure_reward_weights("a", uncertain_weight=0.1)
        self.db.set_procedure_reward_weights("b", uncertain_weight=0.9)
        configs = self.db.list_procedure_reward_configs()
        names = {c["procedure_tag"] for c in configs}
        self.assertEqual(names, {"a", "b"})

    def test_negative_fail_weight_penalty(self):
        # Allow caller to use negative weights to actively penalize failures
        self.db.set_procedure_reward_weights("penalty-proc", fail_weight=-1.0)
        w = self.db.get_procedure_reward_weights("penalty-proc")
        self.assertEqual(w["fail_weight"], -1.0)


class TestComputeVisualReward(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def _make_vs(self, results):
        vsid = self.db.create_visual_session(device_type="test")
        for i, r in enumerate(results):
            self.db.log_step_verification(vsid, f"s{i}", r)
        return vsid

    def test_default_reward_formula(self):
        vsid = self._make_vs(["pass", "pass", "uncertain", "fail"])
        # default: (1.0*2 + 0.5*1 + 0.0*1) / 4 = 2.5/4 = 0.625
        r = traj_mod.compute_visual_reward(vsid)
        self.assertAlmostEqual(r, 0.625, places=3)

    def test_custom_weights_strict(self):
        # Make uncertain count as 0.0 — strict procedure
        self.db.set_procedure_reward_weights("strict",
                                             pass_weight=1.0,
                                             uncertain_weight=0.0,
                                             fail_weight=0.0)
        vsid = self._make_vs(["pass", "pass", "uncertain", "fail"])
        r = traj_mod.compute_visual_reward(vsid, procedure_tag="strict")
        # (1.0*2 + 0.0*1 + 0.0*1)/4 = 0.5
        self.assertAlmostEqual(r, 0.5, places=3)

    def test_custom_weights_penalty(self):
        # Penalize failures heavily
        self.db.set_procedure_reward_weights("punish",
                                             pass_weight=1.0,
                                             uncertain_weight=0.5,
                                             fail_weight=-1.0)
        vsid = self._make_vs(["pass", "pass", "fail"])
        r = traj_mod.compute_visual_reward(vsid, procedure_tag="punish")
        # (1.0*2 + 0.5*0 + -1.0*1)/3 = 1/3
        self.assertAlmostEqual(r, 0.3333, places=3)

    def test_no_step_results_returns_none(self):
        vsid = self.db.create_visual_session(device_type="test")
        self.assertIsNone(traj_mod.compute_visual_reward(vsid))


class TestCaptureTrajectoryPicksProcedureTag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    def test_capture_uses_per_procedure_weights(self):
        # Strict surgical-prep config: uncertain counts as 0
        self.db.set_procedure_reward_weights("surgical-prep",
                                             pass_weight=1.0,
                                             uncertain_weight=0.0,
                                             fail_weight=0.0)
        sid = self.db.create_session()
        self.db.append_message(sid, "user", "frame 1")
        self.db.append_message(sid, "assistant", "verified")
        vsid = self.db.create_visual_session(device_type="test")
        self.db.log_step_verification(vsid, "s1", "pass")
        self.db.log_step_verification(vsid, "s2", "uncertain")

        # With default weights this would be (1+0.5)/2 = 0.75
        # With strict weights: (1+0)/2 = 0.5
        tid = traj_mod.capture_trajectory(
            sid, completed=True, model="m",
            visual_session_id=vsid, tags=["surgical-prep"])
        self.assertIsNotNone(tid)

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT reward_signal FROM trajectories WHERE id=?",
                (tid,)).fetchone()
        self.assertAlmostEqual(row["reward_signal"], 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
