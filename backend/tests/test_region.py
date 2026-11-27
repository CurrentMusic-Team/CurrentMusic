#!/usr/bin/env python3
"""IP 属地纯逻辑测试（v1.28.38）。

覆盖两件容易写错的事：哪些地址**不该**查询（私网/回环/保留），以及展示文案
如何从国家/省份归一（中国取省份、国外取国家、繁体国名转简体）。

    tools/run-tests.sh -p test_region.py -v
"""
from __future__ import annotations

import os
import importlib.util
import io
import queue
import re
import sys
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cm_region as R  # noqa: E402
from cm_client_ip import CLOUDFLARE_NETWORKS  # noqa: E402
import test_password_reset as harness  # noqa: E402


class TestPrefix(unittest.TestCase):
    def test_ipv4_24(self):
        self.assertEqual(R.prefix_of("36.59.138.2"), "36.59.138.0/24")

    def test_private_and_loopback_skipped(self):
        for ip in ("10.0.0.1", "192.168.1.1", "172.16.5.5", "127.0.0.1", "::1", "0.0.0.0"):
            self.assertEqual(R.prefix_of(ip), "", ip)

    def test_invalid(self):
        self.assertEqual(R.prefix_of("not-an-ip"), "")
        self.assertEqual(R.prefix_of(""), "")

    def test_cloudflare_nginx_and_backend_ranges_match(self):
        template = Path(__file__).resolve().parents[2] / "deploy/nginx-cloudflare-realip.conf.example"
        configured = re.findall(r"^set_real_ip_from ([^;]+);", template.read_text(), re.M)
        self.assertCountEqual(configured, map(str, CLOUDFLARE_NETWORKS))
        for network in CLOUDFLARE_NETWORKS:
            self.assertEqual(R.prefix_of(network.network_address + 1), "", str(network))

    def test_public_ipv6_and_mapped_addresses(self):
        self.assertEqual(R.prefix_of("2001:4860:1234::5"), "2001:4860:1234::/48")
        self.assertEqual(R.prefix_of("::ffff:36.59.138.2"), "36.59.138.0/24")
        for ip in ("::ffff:104.16.1.1", "100.64.0.1", "240.0.1.2", "ff02::1"):
            self.assertEqual(R.prefix_of(ip), "", ip)


class TestRegionRefresh(unittest.TestCase):
    _cleanup = harness.TestPasswordReset._cleanup
    call = harness.TestPasswordReset.call

    def setUp(self):
        harness.TestPasswordReset.setUp(self)
        with closing(R.cm_db.connect()) as conn:
            R.init_region_tables(conn)
        self.uid = R.cm_db.create_user("region-fixture", "any-password", "Region Fixture")
        self.token = R.cm_db.issue_token(self.uid)
        self.enterContext(mock.patch.multiple(R, _last_note={}, _pending={}, _jobs=queue.Queue()))
        self.enterContext(mock.patch.object(R, "_ensure_worker"))
        self.lookup = self.enterContext(mock.patch.object(R, "_lookup_raw"))

    def test_edge_preserves_region_and_does_not_delay_new_prefix(self):
        R._remember("36.59.138.0/24", "安徽")
        R._remember("2001:4860:1234::/48", "美国")
        self.call("GET", "/auth/me", token=self.token, ip="36.59.138.2")
        self.assertEqual(self.call("GET", f"/users/{self.uid}/profile")[1]["user"]["ipRegion"], "安徽")

        for address, prefix in (("104.16.1.2", "104.16.1.0/24"), ("2606:4700::1234", "2606:4700::/48")):
            R._remember(prefix, "英国")
            self.call("GET", "/auth/me", token=self.token, ip=address)
        self.assertTrue(R._jobs.empty())
        self.lookup.assert_not_called()
        self.assertEqual(R.cm_db.public_profile(self.uid)["user"]["ipRegion"], "安徽")

        address = "2001:4860:1234::5"
        self.call("GET", "/auth/me", token=self.token, ip=address)
        me = self.call("GET", "/auth/me", token=self.token, ip=address)[1]
        profile = self.call("GET", f"/users/{self.uid}/profile")[1]["user"]
        square = self.call("GET", "/users/square?query=Region%20Fixture")[1]
        self.assertEqual([me["ipRegion"], profile["ipRegion"], square["users"][0]["ipRegion"]], ["美国"] * 3)

    def test_outdated_lookup_does_not_overwrite_new_prefix(self):
        R.note(self.uid, "36.59.138.2")
        old_job = R._jobs.get_nowait()
        R._remember("2001:4860:1234::/48", "美国")

        def old_result(ip):
            R.note(self.uid, "2001:4860:1234::5")
            return "安徽", False

        self.lookup.side_effect = old_result
        R._resolve(*old_job)
        self.lookup.assert_called_once_with("36.59.138.2")
        self.assertEqual(R.cm_db.public_profile(self.uid)["user"]["ipRegion"], "美国")

    def test_users_share_a_lookup_and_failures_retry_after_cooldown(self):
        second_uid = R.cm_db.create_user("region-other", "any-password", "Region Other")
        with mock.patch.object(R.time, "monotonic", return_value=1000) as now:
            R.note(self.uid, "36.59.138.2")
            R.note(second_uid, "36.59.138.3")
            self.assertEqual(R._jobs.qsize(), 1)
            self.lookup.return_value = "", False
            R._resolve(*R._jobs.get_nowait())
            self.lookup.assert_called_once()
            R.note(self.uid, "36.59.138.2")
            self.assertTrue(R._jobs.empty())
            now.return_value += R.FAIL_RETRY
            self.lookup.return_value = "安徽", False
            R.note(self.uid, "36.59.138.2")
            R.note(second_uid, "36.59.138.3")
            R._resolve(*R._jobs.get_nowait())
            self.assertEqual(self.lookup.call_count, 2)
        for uid in (self.uid, second_uid):
            self.assertEqual(R.cm_db.public_profile(uid)["user"]["ipRegion"], "安徽")

    def test_backfill_dry_run_filters_edges_and_skips_all_writes_and_lookups(self):
        script = Path(__file__).resolve().parents[2] / "tools/backfill-region.py"
        spec = importlib.util.spec_from_file_location("region_backfill", script)
        backfill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backfill)
        stamp = "17/Oct/2026:15:37:10 +0800"
        log = Path(self.tmp.name) / "fixture.log"
        lines = (
            ("2001:4860:1234::5", 1, 200), ("2606:4700::1", 2, 200),
            ("2001:4860:1234::5", 3, 200), ("104.16.1.2", 3, 200),
            ("36.59.138.2", 4, 401),
        )
        log.write_text("".join(f'{ip} - - [{stamp}] "POST /cm/plays/{song} HTTP/2.0" {code} 1\n'
                               for ip, song, code in lines))
        timestamp = backfill._log_timestamp(stamp)
        self.assertEqual(backfill.log_ips(log), {(1, timestamp): "2001:4860:1234::5"})
        with closing(R.cm_db.connect()) as conn, conn:
            conn.execute("INSERT INTO plays(user_id,ncm_id,played_at) VALUES(?,?,?)", (self.uid, 1, timestamp))
        output = io.StringIO()
        with (mock.patch.object(sys, "argv", [str(script), "--log", str(log), "--dry"]),
              mock.patch.object(R.cm_db, "connect", side_effect=AssertionError("dry run opened writable DB")),
              redirect_stdout(output)):
            self.assertEqual(backfill.main(), 0)
        self.assertIn("可回填用户：1", output.getvalue())
        self.assertNotIn("2001:4860", output.getvalue())
        self.lookup.assert_not_called()
        self.assertEqual(R.cm_db.public_profile(self.uid)["user"]["ipRegion"], "")


class TestHosting(unittest.TestCase):
    """机房/代理出口的 IP 不能当成用户真实属地（IDC/代理出口被误标属地的教训）。"""

    def test_idc_asn_keyword(self):
        self.assertTrue(R.looks_hosting({"as": "AS36530 IDC Cube", "hosting": False}))

    def test_akile_hk_vps(self):
        self.assertTrue(R.looks_hosting({"org": "Akile LTD", "hosting": True}))

    def test_residential_not_hosting(self):
        d = {"isp": "China Telecom", "as": "AS4134 CHINANET", "hosting": False, "proxy": False}
        self.assertFalse(R.looks_hosting(d))

    def test_empty_payload_safe(self):
        self.assertFalse(R.looks_hosting(None))
        self.assertFalse(R.looks_hosting({}))


class TestNormalize(unittest.TestCase):
    def test_china_takes_province(self):
        self.assertEqual(R.normalize("中国", "安徽"), "安徽")

    def test_china_without_region(self):
        self.assertEqual(R.normalize("中国", ""), "中国")

    def test_foreign_takes_country(self):
        self.assertEqual(R.normalize("美国", "弗吉尼亚州"), "美国")

    def test_full_province_names_normalized(self):
        """ip-api 时而是「上海市」、时而「江苏」：统一归一到省级短名。"""
        self.assertEqual(R.normalize("中国", "上海市"), "上海")
        self.assertEqual(R.normalize("中国", "河北省"), "河北")
        self.assertEqual(R.normalize("中国", "江苏"), "江苏")
        self.assertEqual(R.normalize("中国", "内蒙古自治区"), "内蒙古")
        self.assertEqual(R.normalize("中国", "广西壮族自治区"), "广西")
        self.assertEqual(R.normalize("中国", "新疆维吾尔自治区"), "新疆")
        self.assertEqual(R.normalize("中国", "宁夏回族自治区"), "宁夏")
        self.assertEqual(R.normalize("中国", "香港特别行政区"), "香港")
        self.assertEqual(R.normalize("中国", "黑龙江省"), "黑龙江")

    def test_all_34_provinces_covered(self):
        """34 个省级行政区逐个验证：短名与「X省/市/自治区」两种写法都能归一。"""
        suffixes = {
            "北京": "市", "天津": "市", "上海": "市", "重庆": "市",
            "河北": "省", "山西": "省", "辽宁": "省", "吉林": "省", "黑龙江": "省",
            "江苏": "省", "浙江": "省", "安徽": "省", "福建": "省", "江西": "省",
            "山东": "省", "河南": "省", "湖北": "省", "湖南": "省", "广东": "省",
            "海南": "省", "四川": "省", "贵州": "省", "云南": "省", "陕西": "省",
            "甘肃": "省", "青海": "省", "台湾": "省",
            "内蒙古": "自治区", "广西": "壮族自治区", "西藏": "自治区",
            "宁夏": "回族自治区", "新疆": "维吾尔自治区",
            "香港": "特别行政区", "澳门": "特别行政区",
        }
        self.assertEqual(len(suffixes), 34)
        for short, suf in suffixes.items():
            self.assertEqual(R.normalize("中国", short), short)
            self.assertEqual(R.normalize("中国", short + suf), short)

    def test_taiwan_name_normalized(self):
        """「中華民國 / 臺灣」统一为主流写法，避免出现敏感或繁体写法。"""
        self.assertEqual(R.normalize("中華民國", "臺灣省 or 台灣省"), "中国台湾")
        self.assertEqual(R.normalize("台灣", ""), "中国台湾")
        self.assertEqual(R.normalize("中國香港", ""), "香港")

    def test_traditional_country_fixed(self):
        self.assertEqual(R.normalize("澳大利亞", "Queensland"), "澳大利亚")
        self.assertEqual(R.normalize("俄羅斯", ""), "俄罗斯")


if __name__ == "__main__":
    unittest.main()
