import json
import tempfile
import time
import unittest
from unittest.mock import patch

from social_read.adapter import DemoAdapter
from social_read.engine import Engine
from social_read.health import RATE_LIMIT_SECONDS
from social_read.model import ReadError


class PlatformHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.temp.name, mode="demo")

    def tearDown(self):
        self.engine.shutdown()
        self.temp.cleanup()

    def task(self, action, args):
        return self.engine.wait(self.engine.submit(action, args)["task_id"], 3)

    def search(self, sites=None):
        return self.task("search", {"platforms": sites or ["weibo"], "query": "PRIVATE_QUERY"})

    def health(self, site="weibo"):
        return next(p for p in self.engine.capabilities()["platforms"] if p["id"] == site)["recent_activity"]

    def test_observations_are_action_specific_private_and_historical(self):
        found = self.search()
        ref = found["result"]["items"][0]["content_ref"]
        self.task("read", {"content_ref": ref})
        before = self.health()
        self.task("read", {"content_ref": ref})  # Cached read must not create live evidence.
        self.assertEqual(before, self.health())
        self.assertEqual(set(before["actions"]), {"search", "read"})
        latest = before["actions"]["read"]["latest"]
        self.assertEqual(latest["mode"], "demo")
        self.assertEqual(latest["revision"], self.engine.revision)
        self.assertNotIn("PRIVATE_QUERY", json.dumps(before))
        self.assertEqual(self.engine.capabilities()["platforms"][0]["live_validation"], "pending")

    def test_rate_limit_blocks_all_actions_and_survives_restart(self):
        class Limited(DemoAdapter):
            def search(self, *args):
                raise ReadError("rate_limited", "secret=PRIVATE")
        self.engine.adapter = Limited()
        self.assertEqual(self.search()["status"], "failed")
        before = self.health()["actions"]["search"]["last_failure"]
        self.engine.shutdown()
        self.engine = Engine(self.temp.name, mode="demo")
        with patch.object(self.engine.adapter, "read") as reader:
            job = self.task("read", {"platform": "weibo", "url": "https://weibo.com/123/AbCd"})
            reader.assert_not_called()
        error = job["result"]["errors"][0]
        self.assertEqual(error["status"], "rate_limited")
        self.assertGreater(error["retry_after_seconds"], 0)
        self.assertEqual(before, self.health()["actions"]["search"]["last_failure"])
        self.assertNotIn("PRIVATE", json.dumps(self.health()))

    def test_cross_instance_cooldown_other_platform_and_expiry(self):
        self.engine._observe("weibo", "search", "failed", "rate_limited")
        other = Engine(self.temp.name, mode="demo")
        try:
            found = other.wait(other.submit("search", {"platforms": ["weibo", "zhihu"], "query": "q"})["task_id"], 3)
            self.assertEqual(found["status"], "partial")
            self.assertEqual([i["platform"] for i in found["result"]["items"]], ["zhihu"])
        finally:
            other.shutdown()
        with patch("social_read.health.time.time", return_value=time.time() + RATE_LIMIT_SECONDS + 1):
            self.assertIsNone(self.health()["cooldown"])
            self.assertEqual(self.search()["status"], "success")
        self.assertIsNotNone(self.health()["actions"]["search"]["last_failure"])
        self.assertIsNotNone(self.health()["actions"]["search"]["last_success"])

    def test_nested_partial_rate_limit_retains_content_and_pauses_platform(self):
        class Partial(DemoAdapter):
            def read(self, site, target, cancel):
                item = super().read(site, target, cancel)
                item["read_errors"] = [{"status": "image_rate_limited", "message": "PRIVATE"}]
                return item
        self.engine.adapter = Partial()
        job = self.task("read", {"platform": "weibo", "url": "https://weibo.com/123/AbCd"})
        self.assertEqual(job["status"], "partial")
        self.assertTrue(job["result"]["content"]["body"])
        self.assertEqual(self.health()["actions"]["read"]["latest"]["status"], "partial")
        self.assertIsNotNone(self.health()["cooldown"])

    def test_partial_comments_and_unknown_error_are_not_success(self):
        class Partial(DemoAdapter):
            def comments(self, *args, **kwargs):
                item = super().comments(*args, **kwargs)
                item["errors"] = [{"status": "verification_required"}]
                return item
        self.engine.adapter = Partial()
        self.task("comments", {"platform": "weibo", "url": "https://weibo.com/123/AbCd"})
        self.assertEqual(self.health()["actions"]["comments"]["latest"]["status"], "partial")
        self.assertIsNone(self.health()["actions"]["comments"]["last_success"])
        self.engine._observe("weibo", "read", "failed", "cookie=PRIVATE")
        self.assertNotIn("PRIVATE", json.dumps(self.health()))

    def test_mode_and_profile_scopes_do_not_share_observations(self):
        self.engine._observe("weibo", "search", "failed", "rate_limited")
        for mode, profile in [("live", None), ("demo", "different")]:
            other = Engine(self.temp.name, mode=mode, profile=profile)
            try:
                self.assertEqual(other._health("weibo")["actions"], {})
                self.assertIsNone(other._health("weibo")["cooldown"])
            finally:
                other.shutdown()

    def test_cancelled_request_does_not_replace_previous_observation(self):
        self.search()
        before = self.health()
        self.engine._observe("weibo", "search", "failed", "cancelled")
        self.assertEqual(before, self.health())

    def test_existing_database_migrates_without_losing_content(self):
        self.search()
        count = len(self.engine.store.history())
        self.engine.store.db.execute("DROP TABLE platform_activity")
        self.engine.store.db.execute("DROP TABLE platform_cooldowns")
        self.engine.store.db.commit()
        self.engine.shutdown()
        self.engine = Engine(self.temp.name, mode="demo")
        self.assertEqual(len(self.engine.store.history()), count)
        self.assertEqual(self.health()["actions"], {})


if __name__ == "__main__":
    unittest.main()
