"""
Cloud sync retry, backoff, and dedup tests.
Mocks urllib.request.urlopen to simulate transient/permanent failures.

Run: python -m pytest tests/test_cloud_sync.py -v
"""
import os
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

# Configure cloud env BEFORE importing cloud_sync
os.environ["OPENEYE_CLOUD_URL"] = "https://cloud.example/openeye"
os.environ["OPENEYE_CLOUD_KEY"] = "test-key"
os.environ["OPENEYE_SYNC_BACKOFF_BASE"] = "0.001"  # speed up tests
os.environ["OPENEYE_SYNC_MAX_RETRIES"] = "3"

# Isolate the database
_TMP = tempfile.mkdtemp(prefix="openeye-cs-")
os.environ["OPENEYE_HOME"] = _TMP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import importlib
import state as state_module  # noqa: E402
state_module._db_instance = None
state_module.DB_PATH = Path(_TMP) / "openeye.db"

import cloud_sync  # noqa: E402
importlib.reload(cloud_sync)  # pick up new env vars


def _fake_response(status: int = 200):
    """Build a minimal urlopen context manager response."""
    resp = mock.MagicMock()
    resp.status = status
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


def _http_error(code: int):
    return urllib.error.HTTPError(
        url="https://cloud.example", code=code, msg=f"err{code}",
        hdrs=None, fp=BytesIO(b'{"detail":"x"}'))


class TestPostOnce(unittest.TestCase):
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_post_once_success(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(200)
        ok = cloud_sync._post_once("ingest/test", [{"id": 1}], "batch-abc")
        self.assertTrue(ok)

    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_post_once_retryable_5xx(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(503)
        with self.assertRaises(cloud_sync.CloudSyncError) as ctx:
            cloud_sync._post_once("ingest/test", [{"id": 1}], "b")
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.status, 503)

    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_post_once_terminal_4xx(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(400)
        with self.assertRaises(cloud_sync.CloudSyncError) as ctx:
            cloud_sync._post_once("ingest/test", [{"id": 1}], "b")
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.status, 400)

    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_post_once_network_error_retryable(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        with self.assertRaises(cloud_sync.CloudSyncError) as ctx:
            cloud_sync._post_once("ingest/test", [{"id": 1}], "b")
        self.assertTrue(ctx.exception.retryable)


class TestPostWithRetry(unittest.TestCase):
    @mock.patch("cloud_sync.time.sleep", return_value=None)
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_retries_then_succeeds(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = [
            _http_error(503),
            _http_error(502),
            _fake_response(200),
        ]
        ok = cloud_sync._post_with_retry("ingest/test", [{"id": 1}], "b", max_retries=3)
        self.assertTrue(ok)
        self.assertEqual(mock_urlopen.call_count, 3)

    @mock.patch("cloud_sync.time.sleep", return_value=None)
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_gives_up_after_max_retries(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(503)
        ok = cloud_sync._post_with_retry("ingest/test", [{"id": 1}], "b", max_retries=2)
        self.assertFalse(ok)
        self.assertEqual(mock_urlopen.call_count, 3)  # initial + 2 retries

    @mock.patch("cloud_sync.time.sleep", return_value=None)
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_terminal_error_skips_retry(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(401)
        ok = cloud_sync._post_with_retry("ingest/test", [{"id": 1}], "b", max_retries=5)
        self.assertFalse(ok)
        self.assertEqual(mock_urlopen.call_count, 1)


class TestBatchIdAndDedup(unittest.TestCase):
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_batch_id_header_sent(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(200)
        cloud_sync._post_once("ingest/test", [{"id": 1}], "batch-xyz")
        req = mock_urlopen.call_args.args[0]
        self.assertEqual(req.headers.get("X-openeye-batch-id"), "batch-xyz")

    def test_strip_internal_fields(self):
        rows = [{"id": 1, "sync_pending": 1, "data": "x", "embedding_ref": "ref"}]
        cleaned = cloud_sync._strip_internal_fields(rows, "frames")
        self.assertNotIn("sync_pending", cleaned[0])
        self.assertNotIn("embedding_ref", cleaned[0])
        self.assertEqual(cleaned[0]["data"], "x")

    def test_strip_internal_fields_non_frame(self):
        rows = [{"id": 1, "sync_pending": 1, "embedding_ref": "keep-for-non-frame"}]
        cleaned = cloud_sync._strip_internal_fields(rows, "trajectories")
        self.assertNotIn("sync_pending", cleaned[0])
        # embedding_ref only stripped from frames
        self.assertEqual(cleaned[0]["embedding_ref"], "keep-for-non-frame")


class TestSyncTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        from state import OpenEyeDB
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    @mock.patch("cloud_sync.time.sleep", return_value=None)
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_sync_marks_rows_synced(self, mock_urlopen, _sleep):
        mock_urlopen.return_value = _fake_response(200)
        # Seed a row marked sync_pending=1
        vsid = self.db.create_visual_session(device_type="test", tenant_id="t")
        self.db._conn.execute(
            "UPDATE visual_sessions SET sync_pending=1 WHERE id=?", (vsid,))
        self.db._conn.commit()

        count = cloud_sync._sync_table("visual_sessions")
        self.assertEqual(count, 1)

        # Row should no longer be pending
        pending = self.db.get_pending_sync("visual_sessions")
        self.assertEqual(len(pending), 0)

    @mock.patch("cloud_sync.time.sleep", return_value=None)
    @mock.patch("cloud_sync.urllib.request.urlopen")
    def test_sync_failure_leaves_rows_pending(self, mock_urlopen, _sleep):
        mock_urlopen.side_effect = _http_error(503)
        vsid = self.db.create_visual_session(device_type="test")
        self.db._conn.execute(
            "UPDATE visual_sessions SET sync_pending=1 WHERE id=?", (vsid,))
        self.db._conn.commit()

        count = cloud_sync._sync_table("visual_sessions")
        self.assertEqual(count, 0)
        pending = self.db.get_pending_sync("visual_sessions")
        self.assertEqual(len(pending), 1)

    def test_sync_table_disabled_returns_zero(self):
        # skills is disabled by default (OPENEYE_SYNC_SKILLS=0)
        self.assertEqual(cloud_sync._sync_table("skills"), 0)


if __name__ == "__main__":
    unittest.main()
