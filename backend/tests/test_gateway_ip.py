#!/usr/bin/env python3
"""Trusted client address selection and RealIP forwarding policy."""
from __future__ import annotations

import os
import sys
import unittest
from http.client import HTTPMessage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cm_ncm_gateway as gw  # noqa: E402
import cm_ncm  # noqa: E402
from unittest import mock  # noqa: E402


class H:
    """最小 handler 桩：client_ip/rate_key 只读 headers 与 client_address。

    注意键名保持与实现里 ``headers.get`` 完全一致的大小写（真实 http.server
    的头查找本身大小写不敏感，这里用普通 dict 模拟即可）。
    """

    def __init__(self, headers=None, addr=("127.0.0.1", 0)):
        self.headers = dict(headers or {})
        self.client_address = addr


class TestRealIpPolicy(unittest.TestCase):
    def test_media_gets_realip_even_for_get(self):
        self.assertTrue(gw.should_forward_real_ip("/song/url", "GET", 0))
        self.assertTrue(gw.should_forward_real_ip("/mv/url", "GET", 0))
        self.assertTrue(gw.should_forward_real_ip("/voice/detail", "GET", 1))

    def test_public_reads_do_not_get_realip(self):
        for tier in (0, 1):
            self.assertFalse(gw.should_forward_real_ip("/song/detail", "GET", tier))

    def test_searches_forward_realip(self):
        for path in ("/search", "/cloudsearch", "/search/suggest", "/search/hot/detail"):
            self.assertTrue(gw.should_forward_real_ip(path, "GET", 0))

    def test_account_writes_get_realip(self):
        self.assertTrue(gw.should_forward_real_ip("/playlist/create", "POST", 2))
        self.assertTrue(gw.should_forward_real_ip("/playlist/tracks", "POST", 2))
        self.assertTrue(gw.should_forward_real_ip("/playlist/create", "DELETE", 2))
        self.assertFalse(gw.should_forward_real_ip("/playlist/detail", "GET", 2))

    def test_client_supplied_realip_is_stripped(self):
        p = gw.normalize_params("/comment", {"realIP": "8.8.8.8", "content": "x"}, "POST")
        self.assertNotIn("realIP", p)

    def test_custom_writes_forward_server_derived_ip(self):
        with mock.patch.object(cm_ncm, "post_json", return_value={"code": 200}) as m:
            cm_ncm.like_song("cookie", 1, True, real_ip="36.59.138.2")
            self.assertEqual(m.call_args.kwargs["real_ip"], "36.59.138.2")
        with mock.patch.object(cm_ncm, "call", return_value={"code": 200, "comment": {"commentId": 9}}) as m:
            cm_ncm.comment_post("cookie", 1, "hi", real_ip="36.59.138.2")
            self.assertEqual(m.call_args.kwargs["real_ip"], "36.59.138.2")
        with mock.patch.object(cm_ncm, "call", return_value={"code": 200}) as m:
            cm_ncm.artist_sub("cookie", 1, True, real_ip="36.59.138.2")
            self.assertEqual(m.call_args.kwargs["real_ip"], "36.59.138.2")


class TestClientIp(unittest.TestCase):
    def test_direct_peer_ignores_all_forwarded_headers(self):
        h = H({"X-Real-IP": "36.59.138.2", "CF-Connecting-IP": "8.8.8.8",
               "X-Forwarded-For": "1.1.1.1"}, addr=("2001:4860::1", 1))
        self.assertEqual(gw.client_ip(h), "2001:4860::1")

    def test_parses_only_bare_addresses(self):
        for invalid in ("1.1.1.1, 8.8.8.8", "[2001:4860::1]:443", "fe80::1%eth0", "unknown"):
            h = H({"X-Real-IP": invalid, "X-Forwarded-For": "2001:4860:0:0::1"})
            self.assertEqual(gw.client_ip(h), "2001:4860::1")
        self.assertEqual(gw.client_ip(H({"X-Real-IP": "::ffff:1.1.1.1"})), "1.1.1.1")

    def test_duplicate_proxy_headers_fall_back_to_socket(self):
        h = H()
        h.headers = HTTPMessage()
        for name in ("X-Real-IP", "X-Forwarded-For"):
            h.headers[name] = "1.1.1.1"
            h.headers[name.lower()] = "8.8.8.8"
        self.assertEqual(gw.client_ip(h), "127.0.0.1")

    def test_x_real_ip_first_and_unforgeable(self):
        """nginx 覆盖写入的 X-Real-IP 压过一切伪造头（CF 头 / XFF 首值）。"""
        h = H({"X-Real-IP": "36.59.138.2",
               "CF-Connecting-IP": "8.8.8.8",
               "X-Forwarded-For": "1.2.3.4, 36.59.138.2"})
        self.assertEqual(gw.client_ip(h), "36.59.138.2")

    def test_forged_cf_always_ignored(self):
        """CF 关闭时 CF-Connecting-IP 原样透传、可任意伪造：链条里根本不采信它。

        有 nginx 追加的 XFF 末跳 → 取末跳；只有 CF 头 → 落到套接字兜底。
        """
        self.assertEqual(
            gw.client_ip(H({"CF-Connecting-IP": "8.8.8.8", "X-Forwarded-For": "36.59.138.2"})),
            "36.59.138.2")
        self.assertEqual(
            gw.client_ip(H({"CF-Connecting-IP": "8.8.8.8"}, addr=("10.0.0.9", 1))),
            "10.0.0.9")

    def test_xff_last_hop_wins_over_forged_first(self):
        """$proxy_add_x_forwarded_for 把真实对端追加在**末尾**，首值不可信。"""
        h = H({"X-Forwarded-For": "8.8.4.4, 36.59.138.2"})
        self.assertEqual(gw.client_ip(h), "36.59.138.2")

    def test_socket_fallback(self):
        self.assertEqual(gw.client_ip(H(addr=("10.0.0.9", 1))), "10.0.0.9")

    def test_rate_key_is_client_ip(self):
        """v1.28.24 起两函数合一：限速键与 realIP 取值链永远一致。"""
        h = H({"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "9.9.9.9, 2.2.2.2"})
        self.assertEqual(gw.rate_key(h), gw.client_ip(h))
        self.assertEqual(gw.rate_key(h), "1.1.1.1")


if __name__ == "__main__":
    unittest.main()
