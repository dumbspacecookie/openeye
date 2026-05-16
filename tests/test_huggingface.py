"""
HuggingFace push: dataset card generation and retry logic tests.
Network calls are fully mocked.

Run: python -m pytest tests/test_huggingface.py -v
"""
import os
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="openeye-hf-")
os.environ["OPENEYE_HOME"] = _TMP
os.environ["OPENEYE_HF_BACKOFF_BASE"] = "0.001"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import importlib
import state as state_module  # noqa: E402
state_module._db_instance = None
state_module.DB_PATH = Path(_TMP) / "openeye.db"

import huggingface as hf  # noqa: E402
importlib.reload(hf)


def _fake_response(body: bytes = b'{"ok": true}'):
    resp = mock.MagicMock()
    resp.read = mock.MagicMock(return_value=body)
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


def _http_error(code: int):
    return urllib.error.HTTPError(
        url="https://huggingface.co/api", code=code, msg="err",
        hdrs=None, fp=BytesIO(b'{"detail":"x"}'))


class TestDatasetCard(unittest.TestCase):
    def test_card_has_yaml_front_matter(self):
        trajs = [{"reward_signal": 0.9, "model": "claude", "completed": 1,
                  "openeye_meta": {"tags": ["hand-hygiene"]}}]
        card = hf.build_dataset_card(trajs, "user/repo", ["medical"])
        self.assertTrue(card.startswith("---\nlicense: mit"))
        self.assertIn("- openeye", card)
        self.assertIn("- medical", card)
        self.assertIn("- procedure-verification", card)

    def test_card_includes_stats(self):
        trajs = [
            {"reward_signal": 0.9, "model": "claude", "completed": 1,
             "openeye_meta": {"tags": ["proc-a"]}},
            {"reward_signal": 0.3, "model": "gpt", "completed": 0,
             "openeye_meta": {"tags": ["proc-a"]}},
        ]
        card = hf.build_dataset_card(trajs, "u/r", [])
        self.assertIn("Trajectories", card)
        self.assertIn("1 completed", card)
        self.assertIn("1 abandoned", card)
        self.assertIn("proc-a", card)
        self.assertIn("claude", card)
        self.assertIn("gpt", card)


class TestRetryLogic(unittest.TestCase):
    @mock.patch("huggingface.time.sleep", return_value=None)
    @mock.patch("huggingface.urllib.request.urlopen")
    def test_retries_then_succeeds(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = [_http_error(503), _http_error(502), _fake_response()]
        result = hf._hf_request("POST", "https://hf/api/test", "tok",
                                body={"x": 1}, max_retries=3)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_urlopen.call_count, 3)

    @mock.patch("huggingface.time.sleep", return_value=None)
    @mock.patch("huggingface.urllib.request.urlopen")
    def test_401_raises_permission_immediately(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(401)
        with self.assertRaises(PermissionError):
            hf._hf_request("POST", "https://hf/api/test", "bad-tok", max_retries=3)
        # No retry on 401
        self.assertEqual(mock_urlopen.call_count, 1)

    @mock.patch("huggingface.time.sleep", return_value=None)
    @mock.patch("huggingface.urllib.request.urlopen")
    def test_403_raises_permission(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(403)
        with self.assertRaises(PermissionError):
            hf._hf_request("POST", "https://hf/api/test", "tok", max_retries=3)
        self.assertEqual(mock_urlopen.call_count, 1)

    @mock.patch("huggingface.time.sleep", return_value=None)
    @mock.patch("huggingface.urllib.request.urlopen")
    def test_404_raises_runtime_no_retry(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(404)
        with self.assertRaises(RuntimeError):
            hf._hf_request("GET", "https://hf/api/test", "tok", max_retries=3)
        self.assertEqual(mock_urlopen.call_count, 1)

    @mock.patch("huggingface.time.sleep", return_value=None)
    @mock.patch("huggingface.urllib.request.urlopen")
    def test_network_error_retries(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = [
            urllib.error.URLError("conn refused"),
            urllib.error.URLError("conn refused"),
            _fake_response(),
        ]
        result = hf._hf_request("GET", "https://hf/api/test", "tok", max_retries=3)
        self.assertEqual(result, {"ok": True})

    @mock.patch("huggingface.time.sleep", return_value=None)
    @mock.patch("huggingface.urllib.request.urlopen")
    def test_exhausts_retries_raises_connection_error(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = urllib.error.URLError("down")
        with self.assertRaises(ConnectionError):
            hf._hf_request("GET", "https://hf/api/test", "tok", max_retries=2)
        self.assertEqual(mock_urlopen.call_count, 3)


class TestDryRunPush(unittest.TestCase):
    def setUp(self):
        from state import OpenEyeDB
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_dry_run_does_not_call_api(self):
        self.db.save_trajectory(
            conversations=[{"from": "human", "value": "x"}],
            model="m", completed=True, reward_signal=0.9, tags=["t"])
        with mock.patch("huggingface.urllib.request.urlopen") as mu:
            result = hf.push_trajectories_to_hub(
                "user/repo", "tok", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["would_push"], 1)
        mu.assert_not_called()

    def test_no_trajectories_raises(self):
        with self.assertRaises(ValueError):
            hf.push_trajectories_to_hub("user/repo", "tok", dry_run=True)


if __name__ == "__main__":
    unittest.main()
