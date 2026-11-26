"""官方捐赠渠道 API 的回归测试：提交校验、单号去重、本人历史与管理台列表。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_password_reset import _Handler
import cm_db
import cm_server


class _DonationHandler(_Handler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response_headers = {}

    def send_header(self, key, value):
        self.response_headers[key] = value


class DonationsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(mock.patch.object(cm_db, "DB_PATH", os.path.join(tmp.name, "test.db")))
        self.enterContext(mock.patch.object(cm_server.cm_region, "note"))
        cm_db.init()
        self.uid = cm_db.create_user("donor", "password123", "捐赠用户")
        self.token = cm_db.issue_token(self.uid)
        admin = cm_db.create_user("donationadmin", "password123", "管理")
        cm_db.set_admin(admin, True)
        self.admin_token = cm_db.issue_token(admin)
        owner = cm_db.create_user("donationowner", "password123", "站长")
        cm_db.set_super(owner, True)
        self.super_token = cm_db.issue_token(owner)

    def request(self, method, path, body=None, token=None, *, binary=False):
        h = _DonationHandler(method, path, body, token=token or "")
        try:
            cm_server.route(h, method)
        except cm_server.ApiError as e:
            return e.code, {"error": e.msg}
        if binary:
            return h.status, h.response_headers, h.wfile.getvalue()
        return h.status, json.loads(h.wfile.getvalue())

    def test_qr_is_public(self):
        assets = Path(cm_server.__file__).with_name("assets")
        for path, filename, mime in (
            ("/donation/qr", "donation-qr.jpg", "image/jpeg"),
            ("/donation/qr/bileizhen/wechat", "donation-qr.jpg", "image/jpeg"),
            ("/donation/qr/rcst20/wechat", "donation-rcst20-wechat.png", "image/png"),
            ("/donation/qr/rcst20/alipay", "donation-rcst20-alipay.jpg", "image/jpeg"),
        ):
            with self.subTest(path=path):
                status, headers, image = self.request("GET", path, binary=True)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], mime)
                self.assertEqual(headers["Cache-Control"], "public, max-age=86400")
                self.assertEqual(image, (assets / filename).read_bytes())
        for path in ("/donation/qr/other/wechat", "/donation/qr/bileizhen/alipay", "/donation/qr/rcst20/other"):
            self.assertEqual(self.request("GET", path)[0], 404)
        with mock.patch.object(cm_server.os.path, "isfile", return_value=False):
            self.assertEqual(self.request("GET", "/donation/qr/rcst20/wechat")[0], 404)

    def test_info_requires_login_and_prefills_profile(self):
        self.assertEqual(self.request("GET", "/donation/info")[0], 401)
        status, info = self.request("GET", "/donation/info", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(info["profile"]["username"], "donor")
        self.assertIn("created_at", info["profile"])
        self.assertEqual(info["mine"]["count"], 0)
        self.assertEqual(info["qrPath"], "/donation/qr")
        self.assertEqual(info["recipients"], [
            {"id": "bileizhen", "name": "bileizhen", "methods": [
                {"id": "wechat", "name": "微信支付", "qrPath": "/donation/qr/bileizhen/wechat"},
            ]},
            {"id": "rcst20", "name": "ℝ𝕔𝕤𝕥𝟚𝟘", "methods": [
                {"id": "wechat", "name": "微信支付", "qrPath": "/donation/qr/rcst20/wechat"},
                {"id": "alipay", "name": "支付宝", "qrPath": "/donation/qr/rcst20/alipay"},
            ]},
        ])

    def test_submit_validation_and_dedupe(self):
        path = "/donation"
        self.assertEqual(self.request("POST", path, {"amount": 5, "txnId": "TX123456"})[0], 401)
        for bad in ({"amount": 0, "txnId": "TX123456"}, {"amount": 5, "txnId": "TX1"},
                    {"amount": 5, "txnId": "TX 12 34"}, {"amount": "5", "txnId": "TX123456"}):
            self.assertEqual(self.request("POST", path, bad, self.token)[0], 400, bad)
        status, r = self.request("POST", path, {"amount": 6.6, "txnId": "TX20261113001", "note": "加油"}, self.token)
        self.assertEqual(status, 200)
        self.assertEqual(r["mine"]["count"], 1)
        self.assertEqual(r["mine"]["totalCents"], 660)
        self.assertEqual([i["txn_id"] for i in r["mine"]["items"]], ["TX20261113001"])
        self.assertEqual(r["mine"]["items"][0]["recipient_id"], "bileizhen")
        self.assertIsNone(r["mine"]["items"][0]["payment_method"])
        # 同一单号重复提交 → 409
        self.assertEqual(self.request("POST", path, {"amount": 6.6, "txnId": "TX20261113001"}, self.token)[0], 409)

    def test_submit_records_destination_and_rejects_invalid_pairs(self):
        path = "/donation"
        body = {"amount": 5, "txnId": "TX-DONATE-0"}
        for fields in (
            {"recipientId": "rcst20"}, {"paymentMethod": "wechat"},
            {"recipientId": None, "paymentMethod": None},
            {"recipientId": ["rcst20"], "paymentMethod": "alipay"},
            {"recipientId": "other", "paymentMethod": "wechat"},
            {"recipientId": "bileizhen", "paymentMethod": "alipay"},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.request("POST", path, {**body, **fields}, self.token)[0], 400)
        for index, (recipient, method) in enumerate(
            (("bileizhen", "wechat"), ("rcst20", "wechat"), ("rcst20", "alipay"))
        ):
            txn = f"TX-DONATE-{index}"
            fields = {"amount": 5, "txnId": txn, "recipientId": recipient, "paymentMethod": method}
            status, result = self.request("POST", path, fields, self.token)
            self.assertEqual(status, 200)
            row = next(row for row in result["mine"]["items"] if row["txn_id"] == txn)
            self.assertEqual((row["recipient_id"], row["payment_method"]), (recipient, method))
        dupe = {**body, "recipientId": "rcst20", "paymentMethod": "alipay"}
        self.assertEqual(self.request("POST", path, dupe, self.token)[0], 409)
        _, info = self.request("GET", "/donation/info", token=self.token)
        _, admin = self.request("GET", "/admin/donations", token=self.admin_token)
        self.assertEqual(admin["recipients"], info["recipients"])
        self.assertEqual({(row["recipient_id"], row["payment_method"]) for row in admin["items"]},
                         {("bileizhen", "wechat"), ("rcst20", "wechat"), ("rcst20", "alipay")})

    def test_migration_backfills_legacy_self_reports_only(self):
        donor = cm_db.get_user(self.uid)
        cm_db.add_donation(donor, 200, "TX-LEGACY-SELF")
        cm_db.add_donation(donor, 300, "TX-LEGACY-ADMIN", created_by=self.uid + 1)
        conn = cm_db.connect()
        try:
            conn.execute("ALTER TABLE donations DROP COLUMN recipient_id")
            conn.execute("ALTER TABLE donations DROP COLUMN payment_method")
            conn.commit()
        finally:
            conn.close()
        cm_db.init()
        cm_db.add_donation(donor, 400, "TX-NEW", recipient_id="rcst20", payment_method="wechat")
        cm_db.init()
        history = cm_db.user_donations(self.uid)
        self.assertEqual((history["count"], history["totalCents"]), (3, 900))
        self.assertEqual({row["txn_id"]: (row["recipient_id"], row["payment_method"])
                          for row in history["items"]}, {
            "TX-LEGACY-SELF": ("bileizhen", None),
            "TX-LEGACY-ADMIN": (None, None),
            "TX-NEW": ("rcst20", "wechat"),
        })

    def test_admin_list_and_delete(self):
        self.assertEqual(self.request("GET", "/admin/donations", token=self.token)[0], 403)
        self.request("POST", "/donation", {"amount": 10, "txnId": "TXA000001", "note": "第一次"}, self.token)
        self.request("POST", "/donation", {"amount": 20, "txnId": "TXA000002"}, self.token)
        status, d = self.request("GET", "/admin/donations?sort=amount", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual((d["totalCount"], d["donors"], d["totalCents"]), (2, 1, 3000))
        self.assertEqual(len(d["items"]), 2)
        it = d["items"][0]
        self.assertEqual((it["username"], it["userCount"], it["userTotalCents"]), ("donor", 2, 3000))
        self.assertEqual(it["amount_cents"], 2000)   # user_total 排序后首条为金额大的一笔
        # 搜索：按备注 / 单号 / 用户名
        _, s = self.request("GET", "/admin/donations?query=TXA000002", token=self.admin_token)
        self.assertEqual(s["total"], 1)
        _, s = self.request("GET", "/admin/donations?query=donor", token=self.admin_token)
        self.assertEqual(s["total"], 2)
        # 删除
        self.assertEqual(self.request("DELETE", f"/admin/donations/{it['id']}", token=self.admin_token)[0], 200)
        _, d2 = self.request("GET", "/admin/donations", token=self.admin_token)
        self.assertEqual((d2["totalCount"], d2["totalCents"]), (1, 1000))
        self.assertEqual(self.request("DELETE", f"/admin/donations/{it['id']}", token=self.admin_token)[0], 404)


    def test_admin_create_donation(self):
        path = "/admin/donations"
        # 普通用户/普通管理员都不能代录
        self.assertEqual(self.request("POST", path, {"username": "donor", "amount": 1}, self.token)[0], 403)
        self.assertEqual(self.request("POST", path, {"username": "donor", "amount": 1}, self.admin_token)[0], 403)
        for bad in ({"amount": 1}, {"username": "nobody", "amount": 1},
                    {"username": "donor"}, {"username": "donor", "amount": 0},
                    {"username": "donor", "amount": 1, "txnId": "T1"},
                    {"username": "donor", "amount": 1, "paymentMethod": "wechat"},
                    {"username": "donor", "amount": 1, "recipientId": "bileizhen", "paymentMethod": "alipay"}):
            # 缺少/找不到目标用户 → 404；金额或单号非法 → 400
            self.assertIn(self.request("POST", path, bad, self.super_token)[0], (400, 404), bad)
        # 按用户名代录，单号自动生成
        status, r = self.request("POST", path, {"username": "donor", "amount": 8.8, "note": "线下"}, self.super_token)
        self.assertEqual(status, 200)
        self.assertTrue(r["txnId"].startswith("ADMIN-"))
        manual_txn = r["txnId"]
        # 按 userId 代录并指定单号；重复单号 409
        status, r = self.request("POST", path, {
            "userId": self.uid, "amount": 5, "txnId": "TXB000001", "recipientId": "rcst20", "paymentMethod": "alipay",
        }, self.super_token)
        self.assertEqual((status, r["txnId"]), (200, "TXB000001"))
        self.assertEqual(self.request("POST", path, {"userId": self.uid, "amount": 5, "txnId": "TXB000001"},
                                      self.super_token)[0], 409)
        self.assertEqual(self.request("POST", path, {
            "userId": self.uid, "amount": 5, "txnId": "TXB000002", "recipientId": "bileizhen",
        }, self.super_token)[0], 200)
        # 列表带代录标记与操作人
        _, d = self.request("GET", "/admin/donations", token=self.admin_token)
        by_txn = {x["txn_id"]: x for x in d["items"]}
        self.assertTrue(by_txn["TXB000001"]["created_by"] > 0)
        self.assertEqual(by_txn["TXB000001"]["created_by_name"], "donationowner")
        self.assertEqual((by_txn["TXB000001"]["recipient_id"], by_txn["TXB000001"]["payment_method"]),
                         ("rcst20", "alipay"))
        self.assertEqual((by_txn["TXB000002"]["recipient_id"], by_txn["TXB000002"]["payment_method"]),
                         ("bileizhen", None))
        self.assertEqual((by_txn[manual_txn]["recipient_id"], by_txn[manual_txn]["payment_method"]), (None, None))
        # 自助提交的记录 created_by 为 0
        self.request("POST", "/donation", {"amount": 1, "txnId": "TXC000001"}, self.token)
        _, d = self.request("GET", "/admin/donations", token=self.admin_token)
        by_txn = {x["txn_id"]: x for x in d["items"]}
        self.assertEqual(by_txn["TXC000001"]["created_by"], 0)


if __name__ == "__main__":
    unittest.main()
