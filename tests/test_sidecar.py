"""
OpenEye sidecar unit tests.
Tests the state engine, trajectories, skills, and DPO export without API keys.

Run: python -m pytest tests/test_sidecar.py -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add sidecar to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

from state import OpenEyeDB


class TestStateEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_create_session(self):
        sid = self.db.create_session(source="test", tenant_id="t1")
        self.assertIsNotNone(sid)
        self.assertEqual(len(sid), 36)  # UUID

    def test_append_and_get_messages(self):
        sid = self.db.create_session()
        self.db.append_message(sid, "user", "hello")
        self.db.append_message(sid, "assistant", "hi there")
        msgs = self.db.get_messages(sid)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[1]["content"], "hi there")

    def test_end_session(self):
        sid = self.db.create_session()
        self.db.end_session(sid, reason="completed")
        # No error means success

    def test_create_visual_session(self):
        vsid = self.db.create_visual_session(
            device_type="hololens", procedure_id="hand-hygiene",
            procedure_name="Hand Hygiene", tenant_id="t1")
        self.assertIsNotNone(vsid)

    def test_log_frame(self):
        vsid = self.db.create_visual_session(device_type="webxr")
        fid = self.db.log_frame(
            visual_session_id=vsid, sequence_num=1,
            scene_description="operator washing hands",
            objects_detected=["hands", "soap", "water"])
        self.assertIsNotNone(fid)
        self.assertIsInstance(fid, int)

    def test_log_step_verification(self):
        vsid = self.db.create_visual_session(device_type="ios")
        vid = self.db.log_step_verification(
            visual_session_id=vsid, step_id="hh-wash",
            result="pass", confidence=0.95,
            reasoning="Both hands visible under running water")
        self.assertIsNotNone(vid)

    def test_step_verification_updates_counts(self):
        vsid = self.db.create_visual_session(device_type="android")
        self.db.log_step_verification(vsid, "s1", "pass")
        self.db.log_step_verification(vsid, "s2", "fail")
        self.db.log_step_verification(vsid, "s3", "pass")
        vs = self.db.get_visual_session(vsid)
        self.assertEqual(vs["step_count"], 3)
        self.assertEqual(vs["steps_verified"], 2)  # only passes

    def test_upsert_skill(self):
        sid = self.db.upsert_skill("test-skill", "some content", description="a test skill")
        self.assertIsNotNone(sid)
        skill = self.db.get_skill("test-skill")
        self.assertEqual(skill["content"], "some content")
        self.assertEqual(skill["use_count"], 1)

    def test_upsert_skill_update(self):
        self.db.upsert_skill("test-skill", "v1")
        self.db.upsert_skill("test-skill", "v2")
        skill = self.db.get_skill("test-skill")
        self.assertEqual(skill["content"], "v2")

    def test_save_trajectory(self):
        convos = [{"from": "system", "value": "test"}, {"from": "human", "value": "hello"}]
        tid = self.db.save_trajectory(
            conversations=convos, model="test-model", completed=True,
            tags=["hand-hygiene"], reward_signal=0.95)
        self.assertIsNotNone(tid)

    def test_export_trajectories_jsonl(self):
        convos = [{"from": "human", "value": "test"}]
        self.db.save_trajectory(conversations=convos, model="m1", completed=True,
                                reward_signal=0.9, tags=["tag1"])
        self.db.save_trajectory(conversations=convos, model="m2", completed=False,
                                reward_signal=0.3)

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            out = f.name
        try:
            count = self.db.export_trajectories_jsonl(out, completed_only=True)
            self.assertEqual(count, 1)
            with open(out) as f:
                line = json.loads(f.readline())
            self.assertEqual(line["model"], "m1")
            self.assertTrue(line["completed"])
            self.assertAlmostEqual(line["openeye_meta"]["reward_signal"], 0.9)
        finally:
            os.unlink(out)

    def test_fts_search_messages(self):
        sid = self.db.create_session()
        self.db.append_message(sid, "user", "verify hand hygiene step three")
        self.db.append_message(sid, "assistant", "step three appears complete")
        results = self.db.search_messages("hand hygiene")
        self.assertGreater(len(results), 0)

    def test_fts_search_frames(self):
        vsid = self.db.create_visual_session(device_type="hololens")
        self.db.log_frame(vsid, 1, "operator scrubbing hands with antiseptic soap")
        results = self.db.search_frames("antiseptic soap")
        self.assertGreater(len(results), 0)

    def test_list_sessions(self):
        self.db.create_session(source="test", tenant_id="t1")
        self.db.create_session(source="test", tenant_id="t2")
        sessions = self.db.list_sessions(tenant_id="t1")
        self.assertEqual(len(sessions), 1)

    def test_sanitize_fts_query(self):
        self.assertEqual(OpenEyeDB._sanitize_fts('test+"injection'), "test  injection")
        self.assertEqual(OpenEyeDB._sanitize_fts("AND test"), "test")


class TestTrajectories(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        os.environ["OPENEYE_HOME"] = os.path.dirname(self.tmp.name)

        import importlib
        import state
        state._db_instance = None
        state.DB_PATH = Path(self.tmp.name)
        importlib.reload(state)
        self.db = state.get_db()
        self.db.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_messages_to_sharegpt(self):
        from trajectories import messages_to_sharegpt
        msgs = [
            {"role": "user", "content": "verify step 1"},
            {"role": "assistant", "content": "step 1 is complete"},
        ]
        result = messages_to_sharegpt(msgs, system_prompt="you are an assistant")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["from"], "system")
        self.assertEqual(result[1]["from"], "human")
        self.assertEqual(result[2]["from"], "gpt")

    def test_compute_visual_reward(self):
        from trajectories import compute_visual_reward
        vsid = self.db.create_visual_session(device_type="test")
        self.db.log_step_verification(vsid, "s1", "pass")
        self.db.log_step_verification(vsid, "s2", "pass")
        self.db.log_step_verification(vsid, "s3", "fail")
        reward = compute_visual_reward(vsid)
        self.assertAlmostEqual(reward, 2 / 3, places=3)


class TestDPOExport(unittest.TestCase):
    def test_sharegpt_to_trl(self):
        from dpo_export import sharegpt_to_trl
        msgs = [
            {"from": "system", "value": "sys prompt"},
            {"from": "human", "value": "user msg"},
            {"from": "gpt", "value": "assistant msg"},
        ]
        result = sharegpt_to_trl(msgs)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["role"], "system")
        self.assertEqual(result[1]["role"], "user")
        self.assertEqual(result[2]["role"], "assistant")

    def test_build_dpo_pairs(self):
        from dpo_export import build_dpo_pairs
        trajs = [
            {"conversations": [{"from": "human", "value": "good"}],
             "tags": '["hand-hygiene"]', "reward_signal": 0.95},
            {"conversations": [{"from": "human", "value": "bad"}],
             "tags": '["hand-hygiene"]', "reward_signal": 0.2},
        ]
        pairs = build_dpo_pairs(trajs)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["openeye_meta"]["procedure"], "hand-hygiene")
        self.assertGreater(pairs[0]["openeye_meta"]["chosen_reward"], 0.8)
        self.assertLess(pairs[0]["openeye_meta"]["rejected_reward"], 0.4)

    def test_no_pairs_below_threshold(self):
        from dpo_export import build_dpo_pairs
        trajs = [
            {"conversations": [{"from": "human", "value": "ok"}],
             "tags": '["proc"]', "reward_signal": 0.5},
        ]
        pairs = build_dpo_pairs(trajs)
        self.assertEqual(len(pairs), 0)

    def test_trl_role_values(self):
        from dpo_export import build_dpo_pairs
        trajs = [
            {"conversations": [{"from": "system", "value": "sys"}, {"from": "human", "value": "q"}, {"from": "gpt", "value": "a"}],
             "tags": '["test"]', "reward_signal": 0.95},
            {"conversations": [{"from": "human", "value": "q"}, {"from": "gpt", "value": "bad"}],
             "tags": '["test"]', "reward_signal": 0.1},
        ]
        pairs = build_dpo_pairs(trajs)
        self.assertEqual(len(pairs), 1)
        valid_roles = {"user", "assistant", "system"}
        for msg in pairs[0]["chosen"]:
            self.assertIn(msg["role"], valid_roles)
        for msg in pairs[0]["rejected"]:
            self.assertIn(msg["role"], valid_roles)


class TestSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))

        import state
        state._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_recall_relevant_skills(self):
        from skills import recall_relevant_skills
        self.db.upsert_skill("hand-hygiene-check", "how to verify hand washing",
                             description="hand hygiene verification", domain="medical")
        self.db.upsert_skill("equipment-check", "how to check equipment",
                             description="equipment pre-op check", domain="manufacturing")

        results = recall_relevant_skills("verify hand hygiene procedure")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "hand-hygiene-check")


if __name__ == "__main__":
    unittest.main()
