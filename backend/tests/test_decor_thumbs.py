#!/usr/bin/env python3
"""挂件缩略图：档位、生成、缓存与回退。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cm_decor  # noqa: E402


class TestThumbSize(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(cm_decor.thumb_size("1"), 32)
        self.assertEqual(cm_decor.thumb_size("33"), 48)
        self.assertEqual(cm_decor.thumb_size("100"), 128)
        self.assertEqual(cm_decor.thumb_size("999"), 256)

    def test_invalid(self):
        for value in (None, "", "abc", "0", "-4"):
            self.assertIsNone(cm_decor.thumb_size(value))


class TestThumbGeneration(unittest.TestCase):
    def setUp(self):
        try:
            from PIL import Image
        except Exception:  # noqa: BLE001  —— PIL 可选
            self.skipTest("Pillow unavailable")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        src_dir = os.path.join(self.tmp.name, "decor")
        thumb_dir = os.path.join(self.tmp.name, "thumbs")
        os.makedirs(src_dir)
        gif = os.path.join(src_dir, "testdecor.gif")
        # 随机噪声让 512px 源图有足够体积，缩到 256px 才能明显变小。
        frame = Image.frombytes("RGBA", (512, 512), os.urandom(512 * 512 * 4))
        frame.save(gif, save_all=True, append_images=[frame], duration=100, loop=0)
        manifest = os.path.join(self.tmp.name, "manifest.json")
        with open(manifest, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "testdecor", "name": "test", "file": "testdecor.gif",
                "format": "gif", "width": 512, "height": 512, "scale": 1.0,
                "bytes": os.path.getsize(gif),
            }], f)

        self._old = (cm_decor.MANIFEST, cm_decor.IMAGE_DIRS, cm_decor.THUMB_DIR)
        cm_decor.MANIFEST = manifest
        cm_decor.IMAGE_DIRS = [src_dir]
        cm_decor.THUMB_DIR = thumb_dir
        cm_decor._catalog, cm_decor._by_id, cm_decor._manifest_stamp = [], {}, (0, 0)

        def restore():
            cm_decor.MANIFEST, cm_decor.IMAGE_DIRS, cm_decor.THUMB_DIR = self._old
            cm_decor._catalog, cm_decor._by_id, cm_decor._manifest_stamp = [], {}, (0, 0)
        self.addCleanup(restore)
        self.thumb_dir = thumb_dir

    def test_default_returns_resized_original_format(self):
        data, ctype = cm_decor.open_image("testdecor")
        self.assertGreater(len(data), 0)
        self.assertEqual(ctype, "image/gif")
        self.assertTrue(os.path.isfile(os.path.join(self.thumb_dir, "testdecor-256.gif")))
        self.assertLess(len(data), os.path.getsize(
            os.path.join(cm_decor.IMAGE_DIRS[0], "testdecor.gif")))

    def test_size_request_prefers_webp(self):
        data, ctype = cm_decor.open_image("testdecor", max_px=32, want_webp=True)
        self.assertGreater(len(data), 0)
        self.assertIn(ctype, ("image/webp", "image/gif"))
        ext = "webp" if ctype == "image/webp" else "gif"
        self.assertTrue(os.path.isfile(os.path.join(self.thumb_dir, f"testdecor-32.{ext}")))

    def test_missing_decoration(self):
        self.assertEqual(cm_decor.open_image("nosuchid"), (None, None))


if __name__ == "__main__":
    unittest.main()
