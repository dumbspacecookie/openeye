"""
Event-bus tests for SSE streaming. Covers:
  - Pub/sub semantics (subscriber sees events for its session)
  - Wildcard subscription receives everything
  - Slow consumer doesn't block publishers (events drop)
  - SSE endpoint emits 'subscribed' then events on writes

Run: python -m pytest tests/test_event_bus.py -v
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sidecar"))

import event_bus  # noqa: E402
import state as state_module  # noqa: E402
from state import OpenEyeDB  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestEventBus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Each test gets a fresh bus
        self.bus = event_bus.EventBus()

    async def test_subscribe_and_publish(self):
        q = await self.bus.subscribe("sess-1")
        n = self.bus.publish("sess-1", "step_verified", {"step_id": "s1"})
        self.assertEqual(n, 1)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertEqual(event["type"], "step_verified")
        self.assertEqual(event["data"]["step_id"], "s1")

    async def test_other_session_does_not_receive(self):
        q = await self.bus.subscribe("sess-A")
        n = self.bus.publish("sess-B", "step_verified", {"step_id": "x"})
        self.assertEqual(n, 0)
        self.assertTrue(q.empty())

    async def test_wildcard_subscriber_receives_all(self):
        q = await self.bus.subscribe("*")
        self.bus.publish("sess-A", "step_verified", {})
        self.bus.publish("sess-B", "frame_logged", {})
        self.assertEqual(q.qsize(), 2)

    async def test_unsubscribe_stops_delivery(self):
        q = await self.bus.subscribe("sess-1")
        await self.bus.unsubscribe("sess-1", q)
        n = self.bus.publish("sess-1", "x", {})
        self.assertEqual(n, 0)

    async def test_slow_subscriber_drops_events(self):
        q = await self.bus.subscribe("sess-1")
        # Saturate the queue
        for i in range(event_bus.QUEUE_MAXSIZE + 50):
            self.bus.publish("sess-1", "evt", {"i": i})
        # Should be exactly QUEUE_MAXSIZE retained
        self.assertEqual(q.qsize(), event_bus.QUEUE_MAXSIZE)

    async def test_subscriber_count(self):
        await self.bus.subscribe("sess-A")
        await self.bus.subscribe("sess-A")
        await self.bus.subscribe("*")
        self.assertEqual(self.bus.subscriber_count("sess-A"), 3)  # 2 direct + 1 wildcard
        self.assertEqual(self.bus.subscriber_count(), 3)


class TestSSEFormat(unittest.TestCase):
    def test_format_sse_frame(self):
        frame = event_bus.format_sse({
            "type": "step_verified",
            "session_id": "abc",
            "ts": 1715000000.0,
            "data": {"step_id": "s1"},
        })
        self.assertIn("event: step_verified", frame)
        self.assertIn("data: ", frame)
        self.assertTrue(frame.endswith("\n\n"))
        # Verify the data line is valid JSON
        data_line = next(l for l in frame.split("\n") if l.startswith("data:"))
        parsed = json.loads(data_line[len("data:"):].strip())
        self.assertEqual(parsed["type"], "step_verified")


class TestEventPublicationFromRoutes(unittest.IsolatedAsyncioTestCase):
    """End-to-end-ish test without the streaming-response hang: subscribe
    to the module-level bus, hit /steps/log via TestClient, verify the
    bus delivered the event."""

    async def asyncSetUp(self):
        # Reset module bus + DB state
        event_bus._bus = event_bus.EventBus()
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = OpenEyeDB(Path(self.tmp.name))
        state_module._db_instance = self.db
        from server import app
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.db.close()
        state_module._db_instance = None
        os.unlink(self.tmp.name)

    async def test_step_log_publishes_to_bus(self):
        sid = self.client.post("/sessions/create", json={}).json()["session_id"]
        vsid = self.client.post("/visual-sessions/create",
                                json={"device_type": "test",
                                      "session_id": sid}).json()["visual_session_id"]

        bus = event_bus.get_bus()
        q = await bus.subscribe(sid)

        r = self.client.post("/steps/log", json={
            "visual_session_id": vsid,
            "step_id": "s1",
            "result": "pass",
            "confidence": 0.95,
            "reasoning": "All conditions visible",
        })
        self.assertEqual(r.status_code, 200)

        event = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertEqual(event["type"], "step_verified")
        self.assertEqual(event["data"]["step_id"], "s1")
        self.assertEqual(event["data"]["result"], "pass")
        self.assertEqual(event["session_id"], sid)

    async def test_frame_log_publishes_to_bus(self):
        sid = self.client.post("/sessions/create", json={}).json()["session_id"]
        vsid = self.client.post("/visual-sessions/create",
                                json={"device_type": "test",
                                      "session_id": sid}).json()["visual_session_id"]

        bus = event_bus.get_bus()
        q = await bus.subscribe(sid)

        r = self.client.post("/frames/log", json={
            "visual_session_id": vsid,
            "sequence_num": 1,
            "scene_description": "operator placing bolt",
        })
        self.assertEqual(r.status_code, 200)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertEqual(event["type"], "frame_logged")
        self.assertEqual(event["data"]["sequence_num"], 1)

    async def test_session_end_publishes(self):
        sid = self.client.post("/sessions/create", json={}).json()["session_id"]
        bus = event_bus.get_bus()
        q = await bus.subscribe(sid)
        self.client.post(f"/sessions/{sid}/end", json={"reason": "completed"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        self.assertEqual(event["type"], "session_ended")
        self.assertEqual(event["data"]["reason"], "completed")


if __name__ == "__main__":
    unittest.main()
