"""
Context sync tests — default-off behavior, opt-in, PII stripping, retry, marker table.

Run: python -m pytest tests/test_context_sync.py -v
"""
import importlib
import os
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

# Configure env BEFORE importing context_sync
os.environ["OPENEYE_CONTEXT_OPTIN"] = "true"
os.environ["OPENEYE_CONTEXT_API_KEY"] = "ctx-test-key"
os.environ["OPENEYE_CONTEXT_URL"] = "https://api.example/v1/openeye"
os.environ["OPENEYE_CONTEXT_BACKOFF_BASE"] = "0.001"
os.environ["OPENEYE_CONTEXT_MAX_RETRIES"] = "3"
# Skip consent attestation requirement for the test suite
os.environ["OPENEYE_CONTEXT_CONSENT_CONFIRMED"] = "true"

_TMP = tempfile.mkdtemp(prefix="openeye-ctx-")
os.environ["OPENEYE_HOME"] = _TMP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import state as state_module  # noqa: E402
state_module._db_instance = None
state_module.DB_PATH = Path(_TMP) / "openeye.db"

import context_sync  # noqa: E402
importlib.reload(context_sync)


def _fake_response(status: int = 200):
    resp = mock.MagicMock()
    resp.status = status
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


def _http_error(code: int):
    return urllib.error.HTTPError(
        url=context_sync.CONTEXT_URL, code=code, msg=f"err{code}",
        hdrs=None, fp=BytesIO(b'{"detail":"x"}'))


class TestEnableGate(unittest.TestCase):
    def test_is_enabled_when_opted_in_with_key(self):
        # set at module load via env vars
        self.assertTrue(context_sync.is_enabled())

    def test_default_off_means_no_post(self):
        with mock.patch.object(context_sync, "CONTEXT_OPTIN", False), \
             mock.patch("context_sync.urllib.request.urlopen") as mu:
            result = context_sync.sync_once()
            mu.assert_not_called()
            self.assertEqual(result["sent"], 0)
            self.assertFalse(result["enabled"])

    def test_no_key_means_disabled(self):
        with mock.patch.object(context_sync, "CONTEXT_KEY", ""):
            self.assertFalse(context_sync.is_enabled())

    def test_no_consent_means_disabled(self):
        with mock.patch.object(context_sync, "CONSENT_ENV", False), \
             mock.patch("context_sync.os.path.exists", return_value=False):
            self.assertFalse(context_sync.is_enabled())


class TestConsentAttestation(unittest.TestCase):
    def setUp(self):
        self.tmp_marker = tempfile.NamedTemporaryFile(delete=False)
        self.tmp_marker.close()
        os.unlink(self.tmp_marker.name)  # start absent
        self._orig_marker = context_sync.CONSENT_MARKER
        self._orig_consent_env = context_sync.CONSENT_ENV
        context_sync.CONSENT_MARKER = self.tmp_marker.name
        context_sync.CONSENT_ENV = False

    def tearDown(self):
        context_sync.CONSENT_MARKER = self._orig_marker
        context_sync.CONSENT_ENV = self._orig_consent_env
        try:
            os.unlink(self.tmp_marker.name)
        except OSError:
            pass

    def test_no_attestation_by_default(self):
        self.assertFalse(context_sync.has_consent_attestation())

    def test_record_creates_marker(self):
        path = context_sync.record_consent_attestation(note="signed DPA 2026-05-15")
        self.assertEqual(path, self.tmp_marker.name)
        self.assertTrue(context_sync.has_consent_attestation())
        with open(path, "r") as f:
            content = f.read()
        self.assertIn("signed DPA 2026-05-15", content)
        self.assertIn("informed consent", content.lower())

    def test_revoke_removes_marker(self):
        context_sync.record_consent_attestation()
        self.assertTrue(context_sync.has_consent_attestation())
        self.assertTrue(context_sync.revoke_consent_attestation())
        self.assertFalse(context_sync.has_consent_attestation())

    def test_revoke_unknown_returns_false(self):
        self.assertFalse(context_sync.revoke_consent_attestation())

    def test_env_var_overrides_marker(self):
        # Marker absent, but env var set → attested
        with mock.patch.object(context_sync, "CONSENT_ENV", True):
            self.assertTrue(context_sync.has_consent_attestation())

    @mock.patch("context_sync.urllib.request.urlopen")
    def test_sync_blocked_without_consent(self, mock_urlopen):
        # Build a fully-eligible trajectory but withhold consent
        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        from state import OpenEyeDB
        db = OpenEyeDB(Path(tmp_db.name))
        state_module._db_instance = db
        try:
            db.set_tenant_optin("acme", True)
            db.save_trajectory(
                conversations=[{"from": "human", "value": "x"}],
                model="m", completed=True, reward_signal=0.9,
                tags=["proc"], tenant_id="acme")

            result = context_sync.sync_once()
            self.assertEqual(result["sent"], 0)
            self.assertFalse(result["enabled"])
            mock_urlopen.assert_not_called()
        finally:
            db.close()
            os.unlink(tmp_db.name)


class TestPIIStripping(unittest.TestCase):
    def test_strip_strips_system_messages(self):
        msgs = [
            {"from": "system", "value": "You are an assistant for customer Acme Corp."},
            {"from": "human", "value": "frame 1"},
            {"from": "gpt", "value": "ok"},
        ]
        out = context_sync._strip_system_prompts(msgs)
        self.assertEqual(len(out), 2)
        self.assertNotIn("system", [m["from"] for m in out])

    def test_clean_drops_no_reward(self):
        traj = {
            "id": "t1", "model": "m", "completed": 1,
            "reward_signal": None, "tags_list": ["bolt-assembly"],
            "conversations": [{"from": "human", "value": "x"}],
        }
        self.assertIsNone(context_sync._clean_for_context(traj))

    def test_clean_drops_no_procedure(self):
        traj = {
            "id": "t1", "model": "m", "completed": 1,
            "reward_signal": 0.9, "tags_list": ["openeye", "completed"],
            "conversations": [{"from": "human", "value": "x"}],
        }
        self.assertIsNone(context_sync._clean_for_context(traj))

    def test_clean_drops_meta_tags(self):
        traj = {
            "id": "t1", "model": "m", "completed": 1,
            "reward_signal": 0.9,
            "tags_list": ["openeye", "completed", "bolt-assembly"],
            "conversations": [{"from": "human", "value": "x"}],
        }
        cleaned = context_sync._clean_for_context(traj)
        self.assertEqual(cleaned["procedure_tag"], "bolt-assembly")

    def test_clean_strips_pii_fields(self):
        traj = {
            "id": "t1", "model": "m", "completed": 1,
            "reward_signal": 0.9, "tags_list": ["proc"],
            "conversations": [{"from": "human", "value": "x"}],
            "tenant_id": "t-acme", "user_id": "u-bob",
            "visual_session_id": "vs-1", "session_id": "s-1",
            "tags": '["proc"]',
        }
        cleaned = context_sync._clean_for_context(traj)
        for forbidden in ("tenant_id", "user_id", "visual_session_id",
                          "session_id", "tags"):
            self.assertNotIn(forbidden, cleaned,
                             f"{forbidden} must not leave the device")

    def test_clean_strips_system_message_from_conversations(self):
        traj = {
            "id": "t1", "model": "m", "completed": 1, "reward_signal": 0.9,
            "tags_list": ["proc"],
            "conversations": [
                {"from": "system", "value": "secret prompt"},
                {"from": "human", "value": "ok"},
            ],
        }
        cleaned = context_sync._clean_for_context(traj)
        roles = [m["from"] for m in cleaned["conversations"]]
        self.assertNotIn("system", roles)


class TestRetryLogic(unittest.TestCase):
    @mock.patch("context_sync.time.sleep", return_value=None)
    @mock.patch("context_sync.urllib.request.urlopen")
    def test_retries_then_succeeds(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = [
            _http_error(503), _http_error(502), _fake_response(200),
        ]
        ok = context_sync._post_with_retry({"x": 1}, "batch-a", max_retries=3)
        self.assertTrue(ok)
        self.assertEqual(mock_urlopen.call_count, 3)

    @mock.patch("context_sync.time.sleep", return_value=None)
    @mock.patch("context_sync.urllib.request.urlopen")
    def test_terminal_4xx_no_retry(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(401)
        ok = context_sync._post_with_retry({"x": 1}, "batch-b", max_retries=3)
        self.assertFalse(ok)
        self.assertEqual(mock_urlopen.call_count, 1)

    @mock.patch("context_sync.urllib.request.urlopen")
    def test_batch_id_header_present(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(200)
        context_sync._post_once({"hi": 1}, "batch-xyz")
        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.headers.get("X-openeye-batch-id"), "batch-xyz")
        self.assertEqual(req.headers.get("X-openeye-schema"),
                         context_sync.SCHEMA_VERSION)


class TestMarkerTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from state import OpenEyeDB
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_unsent_excludes_already_synced(self):
        tid = self.db.save_trajectory(
            conversations=[{"from": "human", "value": "ok"}, {"from": "gpt", "value": "ok"}],
            model="m", completed=True, reward_signal=0.9, tags=["bolt-assembly"])
        self.db.mark_sent_to_context([tid], batch_id="b1")
        unsent = self.db.get_unsent_to_context()
        self.assertEqual(len(unsent), 0)

    def test_unsent_includes_new_trajectories(self):
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"])
        unsent = self.db.get_unsent_to_context()
        self.assertEqual(len(unsent), 1)

    def test_unsent_filters_incomplete(self):
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}],
            model="m", completed=False, reward_signal=0.9, tags=["proc"])
        unsent = self.db.get_unsent_to_context(completed_only=True)
        self.assertEqual(len(unsent), 0)

    def test_forget_makes_trajectory_resendable(self):
        tid = self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"])
        self.db.mark_sent_to_context([tid], batch_id="b1")
        self.assertEqual(len(self.db.get_unsent_to_context()), 0)
        ok = self.db.forget_context_sync(tid)
        self.assertTrue(ok)
        self.assertEqual(len(self.db.get_unsent_to_context()), 1)

    def test_forget_unknown_returns_false(self):
        self.assertFalse(self.db.forget_context_sync("nonexistent"))


class TestSyncOnceEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from state import OpenEyeDB
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    @mock.patch("context_sync.time.sleep", return_value=None)
    @mock.patch("context_sync.urllib.request.urlopen")
    def test_sync_once_ships_and_marks(self, mock_urlopen, _sleep):
        mock_urlopen.return_value = _fake_response(200)
        self.db.set_tenant_optin("acme-factory", True, note="signed DPA 2026-05-15")
        tid = self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}, {"from": "gpt", "value": "y"}],
            model="m", completed=True, reward_signal=0.9, tags=["bolt-assembly"],
            tenant_id="acme-factory")

        result = context_sync.sync_once()
        self.assertEqual(result["sent"], 1)
        self.assertTrue(result["enabled"])

        # Should not re-ship on subsequent pass
        result2 = context_sync.sync_once()
        self.assertEqual(result2["sent"], 0)

    @mock.patch("context_sync.urllib.request.urlopen")
    def test_sync_once_skips_when_disabled(self, mock_urlopen):
        # Save a trajectory but disable opt-in
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"])
        with mock.patch.object(context_sync, "CONTEXT_OPTIN", False):
            result = context_sync.sync_once()
        self.assertEqual(result["sent"], 0)
        self.assertFalse(result["enabled"])
        mock_urlopen.assert_not_called()

    @mock.patch("context_sync.time.sleep", return_value=None)
    @mock.patch("context_sync.urllib.request.urlopen")
    def test_payload_schema_correct(self, mock_urlopen, _sleep):
        mock_urlopen.return_value = _fake_response(200)
        self.db.set_tenant_optin("acme-factory", True)
        self.db.save_trajectory(
            conversations=[
                {"from": "system", "value": "secret"},
                {"from": "human", "value": "frame"},
                {"from": "gpt", "value": "verify_step pass"},
            ],
            model="claude-sonnet-4-6", completed=True,
            reward_signal=0.92, tags=["bolt-assembly"],
            tenant_id="acme-factory")

        context_sync.sync_once()

        # Inspect the actual POST body
        req = mock_urlopen.call_args.args[0]
        import json as _json
        body = _json.loads(req.data.decode("utf-8"))

        self.assertEqual(body["schema_version"], "1.0")
        self.assertEqual(body["trajectory_count"], 1)
        traj = body["trajectories"][0]
        self.assertEqual(traj["procedure_tag"], "bolt-assembly")
        self.assertEqual(traj["model"], "claude-sonnet-4-6")
        self.assertAlmostEqual(traj["reward_signal"], 0.92)
        roles = [m["from"] for m in traj["conversations"]]
        self.assertNotIn("system", roles)
        # PII fields must not appear
        for forbidden in ("tenant_id", "user_id", "visual_session_id", "session_id"):
            self.assertNotIn(forbidden, traj)


class TestPerTenantOptin(unittest.TestCase):
    """Default-deny per tenant: even with global opt-in, only tenants who
    have an explicit opted_in=1 row contribute trajectories to Context."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from state import OpenEyeDB
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_unknown_tenant_treated_as_opted_out(self):
        self.assertFalse(self.db.is_tenant_opted_in("never-set"))

    def test_no_tenant_id_treated_as_opted_out(self):
        self.assertFalse(self.db.is_tenant_opted_in(None))
        self.assertFalse(self.db.is_tenant_opted_in(""))

    def test_explicit_optin_true(self):
        self.db.set_tenant_optin("acme", True, note="signed 2026-05-15")
        self.assertTrue(self.db.is_tenant_opted_in("acme"))

    def test_explicit_optout(self):
        self.db.set_tenant_optin("acme", True)
        self.db.set_tenant_optin("acme", False, note="revoked 2026-05-20")
        self.assertFalse(self.db.is_tenant_opted_in("acme"))

    def test_optin_is_idempotent(self):
        self.db.set_tenant_optin("acme", True)
        self.db.set_tenant_optin("acme", True)  # second call must not error
        rows = self.db.list_tenant_optins()
        self.assertEqual(len([r for r in rows if r["tenant_id"] == "acme"]), 1)

    @mock.patch("context_sync.time.sleep", return_value=None)
    @mock.patch("context_sync.urllib.request.urlopen")
    def test_non_opted_in_tenant_data_does_not_ship(self, mock_urlopen, _sleep):
        mock_urlopen.return_value = _fake_response(200)
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}, {"from": "gpt", "value": "y"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"],
            tenant_id="not-opted-in")

        result = context_sync.sync_once()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped_no_tenant_optin"], 1)
        mock_urlopen.assert_not_called()

    @mock.patch("context_sync.time.sleep", return_value=None)
    @mock.patch("context_sync.urllib.request.urlopen")
    def test_only_opted_in_tenant_data_ships(self, mock_urlopen, _sleep):
        mock_urlopen.return_value = _fake_response(200)
        self.db.set_tenant_optin("yes-share", True)
        # Yes-tenant trajectory
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "yes"}, {"from": "gpt", "value": "ok"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"],
            tenant_id="yes-share")
        # No-tenant trajectory
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "no"}, {"from": "gpt", "value": "ok"}],
            model="m", completed=True, reward_signal=0.9, tags=["proc"],
            tenant_id="no-share")

        result = context_sync.sync_once()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["skipped_no_tenant_optin"], 1)
        # Both should now be marked as processed so we don't re-evaluate
        self.assertEqual(len(self.db.get_unsent_to_context()), 0)


if __name__ == "__main__":
    unittest.main()
