"""
PII scrubber tests. Covers each regex pattern plus the conversation
helper, plus the on/off switch, plus integration with context_sync.

Run: python -m pytest tests/test_pii_scrub.py -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import pii_scrub  # noqa: E402


class TestRegexPatterns(unittest.TestCase):
    def setUp(self):
        # Make sure scrubbing is enabled for the test
        self._was_enabled = pii_scrub.PII_SCRUB_ENABLED
        pii_scrub.PII_SCRUB_ENABLED = True
        self._backend = pii_scrub.PII_BACKEND
        pii_scrub.PII_BACKEND = "regex"

    def tearDown(self):
        pii_scrub.PII_SCRUB_ENABLED = self._was_enabled
        pii_scrub.PII_BACKEND = self._backend

    def test_email_redacted(self):
        result = pii_scrub.scrub("Contact me at alice@example.com for details")
        self.assertIn("[REDACTED:EMAIL]", result)
        self.assertNotIn("alice@example.com", result)

    def test_phone_redacted(self):
        for phone in ["555-123-4567", "(555) 123-4567", "+1 555 123 4567",
                      "555.123.4567"]:
            with self.subTest(phone=phone):
                result = pii_scrub.scrub(f"Call {phone} for support")
                self.assertIn("[REDACTED:", result)
                self.assertNotIn(phone, result)

    def test_ssn_redacted(self):
        result = pii_scrub.scrub("SSN is 123-45-6789 on file")
        self.assertIn("[REDACTED:SSN]", result)
        self.assertNotIn("123-45-6789", result)

    def test_credit_card_redacted(self):
        result = pii_scrub.scrub("Card 4111 1111 1111 1111 on file")
        self.assertNotIn("4111 1111 1111 1111", result)

    def test_ipv4_redacted(self):
        result = pii_scrub.scrub("Connect to 192.168.1.100 directly")
        self.assertIn("[REDACTED:IP]", result)
        self.assertNotIn("192.168.1.100", result)

    def test_dob_redacted(self):
        cases = [
            "DOB: 1985-03-12",
            "Date of birth 03/12/1985",
            "Patient born on 1985-03-12",
        ]
        for c in cases:
            with self.subTest(text=c):
                result = pii_scrub.scrub(c)
                self.assertNotIn("1985", result)

    def test_dob_does_not_swallow_normal_dates(self):
        # A procedure log timestamp like "Step completed on 2026-05-15"
        # must NOT be redacted — only DOB-cued dates
        result = pii_scrub.scrub("Step completed on 2026-05-15 at 14:30")
        self.assertIn("2026-05-15", result)

    def test_honorific_name_redacted(self):
        cases = [
            ("Dr. Sarah Chen completed the procedure", "Sarah Chen"),
            ("Mr. John Smith was the operator", "John Smith"),
            ("Nurse Patricia O'Brien recorded vitals", "Patricia"),
            ("Surgeon Robert Liu made the incision", "Robert Liu"),
        ]
        for text, pii in cases:
            with self.subTest(text=text):
                result = pii_scrub.scrub(text)
                self.assertNotIn(pii, result)
                self.assertIn("[REDACTED:NAME]", result)

    def test_named_role_redacted(self):
        result = pii_scrub.scrub("Operator John Smith performed step 1")
        self.assertNotIn("John Smith", result)

    def test_address_redacted(self):
        result = pii_scrub.scrub("Located at 123 Main Street near downtown")
        self.assertIn("[REDACTED:ADDRESS]", result)
        self.assertNotIn("123 Main Street", result)

    def test_clean_text_passes_through(self):
        # Procedure-typical text with no PII should be unchanged
        text = ("Operator placed bolt in slot. Hands visible. "
                "Torque wrench engaged. Step 3 complete.")
        result = pii_scrub.scrub(text)
        self.assertEqual(result, text)

    def test_empty_and_none_pass_through(self):
        self.assertIsNone(pii_scrub.scrub(None))
        self.assertEqual(pii_scrub.scrub(""), "")

    def test_multiple_pii_in_one_string(self):
        text = "Dr. Sarah Chen (sarah@hospital.com, 555-123-4567) supervised."
        result = pii_scrub.scrub(text)
        self.assertNotIn("Sarah", result)
        self.assertNotIn("sarah@hospital.com", result)
        self.assertNotIn("555-123-4567", result)


class TestDisableSwitch(unittest.TestCase):
    def test_disabled_returns_input_unchanged(self):
        with mock.patch.object(pii_scrub, "PII_SCRUB_ENABLED", False):
            text = "Email alice@example.com SSN 123-45-6789"
            self.assertEqual(pii_scrub.scrub(text), text)


class TestScrubConversations(unittest.TestCase):
    def setUp(self):
        self._was_enabled = pii_scrub.PII_SCRUB_ENABLED
        pii_scrub.PII_SCRUB_ENABLED = True

    def tearDown(self):
        pii_scrub.PII_SCRUB_ENABLED = self._was_enabled

    def test_redacts_all_messages(self):
        convo = [
            {"from": "human", "value": "Operator alice@example.com is ready"},
            {"from": "gpt", "value": "Dr. Sarah Chen confirmed"},
        ]
        result = pii_scrub.scrub_conversations(convo)
        self.assertNotIn("alice", result[0]["value"])
        self.assertNotIn("Sarah", result[1]["value"])

    def test_does_not_mutate_input(self):
        original = [{"from": "human", "value": "Email: a@b.com"}]
        snapshot = original[0]["value"]
        pii_scrub.scrub_conversations(original)
        # Input must be unchanged
        self.assertEqual(original[0]["value"], snapshot)

    def test_preserves_non_value_fields(self):
        convo = [{"from": "tool", "value": "result", "tool_name": "verify_step"}]
        result = pii_scrub.scrub_conversations(convo)
        self.assertEqual(result[0]["tool_name"], "verify_step")

    def test_disabled_returns_input_unchanged(self):
        with mock.patch.object(pii_scrub, "PII_SCRUB_ENABLED", False):
            convo = [{"from": "human", "value": "alice@b.com"}]
            result = pii_scrub.scrub_conversations(convo)
            self.assertEqual(result[0]["value"], "alice@b.com")


class TestContextSyncIntegration(unittest.TestCase):
    """Verify PII is scrubbed in the actual payload sent to Context."""

    def setUp(self):
        # Need full env setup for context_sync
        os.environ["OPENEYE_CONTEXT_OPTIN"] = "true"
        os.environ["OPENEYE_CONTEXT_API_KEY"] = "ctx-test-key"
        os.environ["OPENEYE_CONTEXT_CONSENT_CONFIRMED"] = "true"
        os.environ["OPENEYE_PII_SCRUB"] = "true"

        import importlib
        import state as state_module
        import context_sync
        importlib.reload(context_sync)

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from state import OpenEyeDB
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db
        self.context_sync = context_sync

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_pii_stripped_before_post(self):
        from io import BytesIO
        import json

        self.db.set_tenant_optin("acme", True)
        self.db.save_trajectory(
            conversations=[
                {"from": "human", "value": "Frame: Dr. Sarah Chen approaching patient"},
                {"from": "gpt", "value": "Verified step pass. Operator alice@example.com confirmed."},
            ],
            model="m", completed=True, reward_signal=0.9,
            tags=["procedure"], tenant_id="acme")

        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            class R:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'{"ok": true}'
            return R()

        with mock.patch("context_sync.urllib.request.urlopen", side_effect=fake_urlopen):
            self.context_sync.sync_once()

        self.assertEqual(len(captured), 1)
        body = json.loads(captured[0].data.decode("utf-8"))
        text_blob = json.dumps(body)
        self.assertNotIn("Sarah", text_blob)
        self.assertNotIn("alice@example.com", text_blob)
        self.assertIn("[REDACTED:", text_blob)


if __name__ == "__main__":
    unittest.main()
