import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cm_x_releases


class CIReleaseCacheTests(TestCase):
    def setUp(self):
        self.cache = cm_x_releases.CIReleaseCache()
        self.now = 10
        clock = patch.object(cm_x_releases.time, "monotonic", side_effect=lambda: self.now)
        self.addCleanup(clock.stop)
        clock.start()
        connection = patch.object(cm_x_releases.http.client, "HTTPSConnection")
        self.addCleanup(connection.stop)
        self.connection = connection.start()
        environment = patch.dict(cm_x_releases.os.environ, {"CM_X_RELEASES_GITHUB_TOKEN": "read-only-fixture"})
        self.addCleanup(environment.stop)
        environment.start()

    def reply(self, status, payload=None, headers=None):
        response = Mock(status=status)
        response.getheader.side_effect = (headers or {}).get
        response.read.return_value = json.dumps(payload).encode()
        self.connection.return_value.getresponse.return_value = response

    def test_concurrent_reads_share_refresh_and_revalidate_with_etag(self):
        ci = {"draft": False, "prerelease": True, "tag_name": "v1.3.6-ci.323", "assets": [{"digest": "sha256:test"}]}
        self.reply(200, [ci, dict(ci, draft=True), dict(ci, tag_name="v1.3.6", prerelease=False)], {"ETag": '"ci"'})
        clients = Barrier(8)

        def read(_):
            clients.wait(timeout=3)
            return self.cache.get()

        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(read, range(8))), [[ci]] * 8)
        self.connection.assert_called_once_with("api.github.com", timeout=5)
        request = self.connection.return_value.request
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer read-only-fixture")
        self.now += 59
        self.assertEqual(self.cache.get(), [ci])
        self.assertEqual(request.call_count, 1)

        self.now += 1
        self.reply(304)
        self.assertEqual(self.cache.get(), [ci])
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["headers"]["If-None-Match"], '"ci"')

    def test_rate_limit_is_shared_and_honors_retry_after(self):
        self.reply(403, headers={"Retry-After": "90"})
        self.assertIsNone(self.cache.get())
        self.now += 89
        self.assertIsNone(self.cache.get())
        self.assertEqual(self.connection.call_count, 1)
        self.now += 1
        self.reply(200, [])
        self.assertEqual(self.cache.get(), [])
        self.assertEqual(self.connection.call_count, 2)

    def test_serves_last_success_during_rate_limit_until_it_expires(self):
        ci = {"draft": False, "prerelease": True, "tag_name": "v1.3.6-ci.323"}
        self.reply(200, [ci], {"ETag": '"ci"'})
        self.assertEqual(self.cache.get(), [ci])
        verified_at = self.now

        self.now += 60
        self.reply(429, headers={"Retry-After": "3600"})
        self.assertEqual(self.cache.get(), [ci])
        self.now = verified_at + 3599
        self.assertEqual(self.cache.get(), [ci])
        self.now += 1
        self.assertIsNone(self.cache.get())
        self.assertEqual(self.connection.call_count, 2)

        self.now = verified_at + 3660
        self.reply(304)
        self.assertEqual(self.cache.get(), [ci])
        self.assertEqual(self.connection.call_count, 3)

    def test_preserves_cached_empty_array_during_an_upstream_error(self):
        self.reply(200, [])
        self.assertEqual(self.cache.get(), [])

        self.now += 60
        self.connection.return_value.getresponse.side_effect = TimeoutError
        self.assertEqual(self.cache.get(), [])
        self.assertEqual(self.cache.get(), [])
        self.assertEqual(self.connection.call_count, 2)
