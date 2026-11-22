#!/usr/bin/env python3
"""serve_file 的 ETag / 304 / Range 回归。

背景：APK 稳定入口改 302 后，版本化文件需要能被 CDN/镜像复用；没有 ETag 时
`no-cache` 只会让每次请求都整包回源。这里用最小 handler 桩直接验证头部与字节。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from email.utils import formatdate

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cm_server  # noqa: E402


class _Handler:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})
        self.status = None
        self.sent = []
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.sent.append((name, value))

    def end_headers(self):
        pass

    def header(self, name, default=None):
        for key, value in self.sent:
            if key.lower() == name.lower():
                return value
        return default


class TestServeFile(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="cm-serve-", suffix=".apk")
        with os.fdopen(fd, "wb") as f:
            f.write(b"0123456789")
        os.utime(self.path, (1_700_000_000, 1_700_000_000))
        self.addCleanup(os.unlink, self.path)

    def _call(self, headers=None, cache="public, max-age=3600", cdn_cache=None):
        h = _Handler(headers)
        cm_server.serve_file(h, self.path, "application/vnd.android.package-archive",
                             cache=cache, cdn_cache=cdn_cache)
        return h

    def test_full_response_has_validators(self):
        h = self._call()
        self.assertEqual(h.status, 200)
        self.assertEqual(h.wfile.getvalue(), b"0123456789")
        self.assertTrue(h.header("ETag"))
        self.assertEqual(h.header("Last-Modified"), formatdate(1_700_000_000, usegmt=True))
        self.assertEqual(h.header("Accept-Ranges"), "bytes")

    def test_if_none_match_returns_304(self):
        first = self._call(cdn_cache="public, max-age=60")
        etag = first.header("ETag")
        h = self._call({"If-None-Match": etag}, cdn_cache="public, max-age=60")
        self.assertEqual(h.status, 304)
        self.assertEqual(h.wfile.getvalue(), b"")
        self.assertEqual(h.header("ETag"), etag)
        self.assertEqual(h.header("CDN-Cache-Control"), "public, max-age=60")

    def test_if_modified_since_returns_304(self):
        h = self._call({"If-Modified-Since": formatdate(1_700_000_000, usegmt=True)})
        self.assertEqual(h.status, 304)
        self.assertEqual(h.wfile.getvalue(), b"")

    def test_range_still_works(self):
        h = self._call({"Range": "bytes=2-4"})
        self.assertEqual(h.status, 206)
        self.assertEqual(h.wfile.getvalue(), b"234")
        self.assertEqual(h.header("Content-Range"), "bytes 2-4/10")

    def test_if_range_mismatch_falls_back_to_full(self):
        h = self._call({"Range": "bytes=2-4", "If-Range": '"stale"'})
        self.assertEqual(h.status, 200)
        self.assertEqual(h.wfile.getvalue(), b"0123456789")

if __name__ == "__main__":
    unittest.main()
