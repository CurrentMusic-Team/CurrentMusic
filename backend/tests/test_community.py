"""Community profile and post API tests using the temporary route test harness."""
import base64
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cm_db
import cm_profile_banner
import cm_server
import test_password_reset as harness


class TestCommunity(unittest.TestCase):
    setUp = harness.TestPasswordReset.setUp
    _cleanup = harness.TestPasswordReset._cleanup
    call = harness.TestPasswordReset.call

    def user(self, name):
        uid = cm_db.create_user(name, "fixture-password", name)
        return uid, cm_db.issue_token(uid, "fixture-device")

    def test_old_posts_migrate_and_current_post_detail_matches_list(self):
        uid, token = self.user("author")
        other_id, other = self.user("reader")
        attachment = {"type": "song", "id": 42, "name": "夜间通勤"}
        with cm_db.connect() as db:
            db.execute("ALTER TABLE posts DROP COLUMN title")
            old_id = db.execute(
                "INSERT INTO posts(user_id, content, created_at) VALUES (?, ?, ?)",
                (uid, "**旧帖**", 123),
            ).lastrowid
        cm_db.init()
        cm_db.init()
        self.assertEqual(self.call("GET", f"/posts/{old_id}")[1]["post"]["title"], "")
        self.assertEqual(self.call("GET", f"/posts/{old_id}")[1]["post"]["content"], "**旧帖**")
        self.assertEqual(self.call("POST", "/posts", {"content": "旧客户端正文"}, token=other)[0], 200)
        payload = {"title": " 夜间通勤  ", "content": "**正文**", "attachments": [attachment]}
        self.assertEqual(self.call("POST", "/posts", payload)[0], 401)
        code, created = self.call("POST", "/posts", payload, token=token)
        self.assertEqual(code, 200)
        post_id = created["id"]
        self.assertEqual(self.call("POST", f"/posts/{post_id}/like", {"on": True}, token=other)[0], 200)
        _, listing = self.call("GET", f"/posts?author={uid}&limit=1", token=other)
        self.assertEqual(listing["total"], 2)
        self.assertEqual(self.call("GET", f"/posts?author={other_id}")[1]["total"], 1)
        _, detail = self.call("GET", f"/posts/{post_id}", token=other)
        self.assertEqual(detail["post"], listing["posts"][0])
        self.assertEqual(detail["post"]["title"], "夜间通勤")
        self.assertEqual(detail["post"]["attachments"], [attachment])
        self.assertTrue(detail["post"]["likedByMe"])
        self.assertFalse(detail["post"]["canDelete"])
        self.assertTrue(self.call("GET", f"/posts/{post_id}", token=token)[1]["post"]["canDelete"])
        self.assertFalse(self.call("GET", f"/posts/{post_id}")[1]["post"]["likedByMe"])
        self.assertEqual(self.call("DELETE", f"/posts/{post_id}", token=other)[0], 403)
        self.assertEqual(self.call("DELETE", f"/posts/{post_id}", token=token)[0], 200)
        self.assertEqual(self.call("GET", f"/posts/{post_id}")[0], 404)
        self.assertEqual(self.call("POST", f"/posts/{post_id}/like", {"on": True}, token=other)[0], 404)

    def test_title_validation_and_comment_cursor(self):
        uid, token = self.user("author")
        for title in ("字" * 81, "两行\n标题", "两行\u2028标题", 80, []):
            self.assertEqual(self.call("POST", "/posts", {"title": title, "content": "正文"}, token=token)[0], 400)
        self.assertEqual(self.call("POST", "/posts", {"title": "仅标题"}, token=token)[0], 400)
        code, data = self.call("POST", "/posts", {"title": "🎧" * 80, "content": "正文"}, token=token)
        self.assertEqual(code, 200)
        post_id = data["id"]
        first = cm_db.post_comment_add(uid, post_id, "第一条")
        rest = [cm_db.post_comment_add(uid, post_id, f"第 {i + 2} 条", first) for i in range(101)]
        route = f"/posts/{post_id}/comments"
        old = self.call("GET", route)[1]
        self.assertEqual(set(old), {"comments"})
        self.assertEqual(len(old["comments"]), 100)
        _, page1 = self.call("GET", route + "?limit=30")
        self.assertEqual(len(page1["comments"]), 30)
        self.assertEqual(page1["nextAfter"], rest[28])
        _, page2 = self.call("GET", route + f"?after={page1['nextAfter']}&limit=100")
        self.assertEqual([c["id"] for c in page2["comments"]], rest[29:])
        self.assertEqual(page2["comments"][0]["replyTo"]["id"], first)
        self.assertIsNone(page2["nextAfter"])
        for query in ("after=-1", "after=999999999999999999999", "limit=0", "limit=101", "limit=no"):
            self.assertEqual(self.call("GET", route + "?" + query)[0], 400)

    def test_banner_upload_replace_remove_and_visibility(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable")

        def encode(color, image_format):
            out = io.BytesIO()
            image = Image.new("RGB", (400, 900), color)
            exif = image.getexif()
            exif[274] = 6
            exif[270] = "fixture metadata"
            image.save(out, image_format, exif=exif)
            return {"data": base64.b64encode(out.getvalue()).decode("ascii")}

        uid, token = self.user("banner-owner")
        other_id, other = self.user("banner-viewer")
        public = f"/users/{uid}/profile"
        banner = "/profile/banner"
        original = encode((250, 40, 15), "JPEG")
        self.assertEqual(self.call("GET", public)[1]["user"]["bannerUrl"], "")
        self.assertEqual(self.call("PUT", banner, original)[0], 401)
        self.assertEqual(self.call("DELETE", banner)[0], 401)
        self.assertEqual(self.call("PUT", banner + "?token=" + token, original)[0], 401)
        code, result = self.call("PUT", banner, original, token=token)
        self.assertEqual(code, 200)
        path = result["bannerUrl"]
        self.assertEqual(self.call("GET", public)[1]["user"]["bannerUrl"], path)
        self.assertEqual(self.call("GET", "/auth/me", token=token)[1]["bannerUrl"], path)
        self.assertEqual(self.call("GET", "/profile", token=token)[1]["bannerUrl"], path)
        self.assertEqual(self.call("DELETE", banner, token=other)[0], 200)
        self.assertEqual(self.call("GET", f"/users/{other_id}/profile")[1]["user"]["bannerUrl"], "")

        handler = harness._Handler("GET", path)
        headers = {}
        handler.send_header = lambda key, value: headers.update({key: value})
        cm_server.route(handler, "GET")
        self.assertEqual(handler.status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        with Image.open(io.BytesIO(handler.wfile.getvalue())) as image:
            self.assertEqual(image.size, (600, 400))
            self.assertFalse(image.getexif())
        cached = harness._Handler("GET", path)
        cached.headers["If-None-Match"] = headers["ETag"]
        cm_server.route(cached, "GET")
        self.assertEqual(cached.status, 304)
        self.assertEqual(cached.wfile.getvalue(), b"")

        for invalid in ({"data": "lol"}, {"data": base64.b64encode(b"x" * (1024 * 1024 + 1)).decode()}, []):
            self.assertEqual(self.call("PUT", banner, invalid, token=token)[0], 400)
            self.assertEqual(self.call("GET", public)[1]["user"]["bannerUrl"], path)
        code, result = self.call("PUT", banner, encode((5, 200, 20), "PNG"), token=token)
        self.assertEqual(code, 200)
        self.assertNotEqual(path, result["bannerUrl"])
        self.assertEqual(self.call("GET", path)[0], 404)
        for _ in range(2):
            self.assertEqual(self.call("DELETE", banner, token=token), (200, {"bannerUrl": ""}))
        self.assertEqual(self.call("GET", result["bannerUrl"])[0], 404)
        self.assertEqual(self.call("GET", public)[1]["user"]["bannerUrl"], "")

    def test_legacy_image_banner_survives_migration(self):
        uid, _ = self.user("legacy-banner-owner")
        version, image = "a" * 24, b"legacy JPEG bytes"
        with cm_db.connect() as db:
            db.execute("ALTER TABLE profile_banners DROP COLUMN content_type")
            db.execute("INSERT INTO profile_banners VALUES (?, ?, ?)", (uid, version, image))
        cm_db.init()
        cm_db.init()
        fields = self.call("GET", f"/users/{uid}/profile")[1]["user"]
        self.assertNotIn("bannerType", fields)
        handler = self.banner_request("GET", fields["bannerUrl"])
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.response_headers["Content-Type"], "image/jpeg")
        self.assertEqual(handler.wfile.getvalue(), image)

    def banner_request(self, method, path, *, token="", data=None, headers=None):
        handler = harness._Handler(method, path, token=token)
        if data is not None:
            handler.rfile = io.BytesIO(data)
            handler.headers.update({"Content-Length": str(len(data)), "Content-Type": "video/mp4"})
        handler.headers.update(headers or {})
        handler.response_headers = {}
        handler.send_header = lambda key, value: handler.response_headers.update({key: value})
        try:
            cm_server.route(handler, method)
        except cm_server.ApiError as exc:
            cm_server.jresp(handler, exc.code, {"error": exc.msg})
        return handler

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_video_banner_normalization_range_and_validation(self):
        uid, token = self.user("video-banner-owner")
        profile = f"/users/{uid}/profile"
        upload = "/profile/banner"

        def make_clip(seconds):
            source = Path(self.tmp.name) / f"source-{seconds}.mov"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=120x200:rate=24",
                            "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
                            "-c:v", "libx264", "-threads", "1", "-c:a", "aac", str(source)], check=True)
            return source.read_bytes()

        source = make_clip(5)
        self.assertEqual(self.banner_request("PUT", upload, data=source).status, 401)
        saved = self.banner_request("PUT", upload, token=token, data=source, headers={"Content-Type": "video/quicktime"})
        self.assertEqual(saved.status, 200, saved.wfile.getvalue())
        result = json.loads(saved.wfile.getvalue())
        path = result["bannerUrl"]
        self.assertEqual(result["bannerType"], "video")
        others = [self.call("GET", profile)[1]["user"], self.call("GET", "/profile", token=token)[1],
                  self.call("GET", "/auth/me", token=token)[1]]
        for fields in others:
            self.assertEqual({key: fields[key] for key in result}, result)

        full = self.banner_request("GET", path)
        data = full.wfile.getvalue()
        self.assertEqual(full.response_headers["Content-Type"], "video/mp4")
        self.assertEqual(full.response_headers["Accept-Ranges"], "bytes")
        self.assertEqual(int(full.response_headers["Content-Length"]), len(data))
        self.assertLess(data.index(b"moov"), data.index(b"mdat"))
        output = Path(self.tmp.name) / "banner.mp4"
        output.write_bytes(data)
        reported = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                            "stream=codec_type,codec_name,width,height,duration", "-of", "json", str(output)])
        self.assertEqual(json.loads(reported)["streams"], [{"codec_name": "h264", "codec_type": "video", "width": 720,
                                                           "height": 480, "duration": "5.000000"}])

        part = self.banner_request("GET", path, headers={"Range": "bytes=0-1"})
        self.assertEqual(part.status, 206)
        self.assertEqual(part.response_headers["Content-Range"], f"bytes 0-1/{len(data)}")
        self.assertEqual(part.wfile.getvalue(), data[:2])
        self.assertEqual(self.banner_request("GET", path, headers={"Range": f"bytes={len(data)}-"}).status, 416)
        self.assertEqual(self.banner_request("GET", path, headers={"If-None-Match": full.response_headers["ETag"]}).status, 304)

        for invalid in (b"not video", make_clip(5.25)):
            self.assertEqual(self.banner_request("PUT", upload, token=token, data=invalid).status, 400)
        oversized = {"Content-Length": str(cm_profile_banner.VIDEO_BYTES + 1)}
        self.assertEqual(self.banner_request("PUT", upload, token=token, data=b"", headers=oversized).status, 413)
        self.assertEqual(self.call("GET", profile)[1]["user"]["bannerUrl"], path)

        self.assertEqual(self.call("DELETE", upload, token=token), (200, {"bannerUrl": ""}))
        self.assertEqual(self.call("GET", path)[0], 404)
