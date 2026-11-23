#!/usr/bin/env python3
"""Provision a protected KuGou session through the local KuGouMusicApi."""

import argparse
import getpass
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(ProxyHandler({}), NoRedirect())
COOKIE_NAMES = {
    "token", "userid", "dfid", "t1", "vip_type", "vip_token", "KUGOU_API_PLATFORM",
    "KUGOU_API_MID", "KUGOU_API_GUID", "KUGOU_API_DEV", "KUGOU_API_MAC", "KUGOU_API_WEBGL",
}


def request(base, path, cookies, body=None):
    headers = {"Cookie": cookie_header(cookies)}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(base + path, headers=headers, data=None if body is None else json.dumps(body).encode())
    try:
        response = OPENER.open(req, timeout=15)
    except HTTPError as exc:
        response = exc
    with response:
        result = json.loads(response.read(256 * 1024))
        if response.status != 200 or result.get("status") != 1 or result.get("error_code"):
            code = result.get("error_code")
            raise ValueError(f"{path} 被拒绝（{code if type(code) is int else response.status}）")
        for line in response.headers.get_all("Set-Cookie", []):
            jar = SimpleCookie(line)
            for key in COOKIE_NAMES.intersection(jar):
                value = jar[key].value
                if value and value not in ("undefined", "null", "-"):
                    cookies[key] = value
        return result.get("data") or {}


def cookie_header(cookies):
    return "; ".join(f"{key}={value}" for key, value in sorted(cookies.items()) if re.fullmatch(r"[\x21-\x3a\x3c-\x7e]+", value))


def save(path, cookies):
    fd, temp = tempfile.mkstemp(prefix=".kugou-cookie-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as output:
            if path.is_file():
                current = path.stat()
                os.fchown(output.fileno(), current.st_uid, current.st_gid)
            output.write(cookie_header(cookies) + "\n")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:3201")
    parser.add_argument("--output", type=Path, default=Path("/etc/currentmusic/kugou-cookie"))
    args = parser.parse_args()
    base = args.base.rstrip("/")
    ref = urlsplit(base)
    if ref.scheme != "http" or ref.hostname != "127.0.0.1" or ref.path or ref.query or ref.fragment or ref.username:
        parser.error("KuGouMusicApi 必须使用 127.0.0.1 的本机地址")
    cookies = {}
    try:
        mobile = getpass.getpass("酷狗手机号：")
        if not re.fullmatch(r"1\d{10}", mobile):
            raise ValueError("手机号格式错误")
        request(base, "/captcha/sent", cookies, {"mobile": mobile})
        print("酷狗已确认发送短信。", flush=True)
        code = getpass.getpass("酷狗验证码：")
        if not re.fullmatch(r"\d{4,8}", code):
            raise ValueError("验证码格式错误")
        request(base, "/login/cellphone", cookies, {"mobile": mobile, "code": code})
        if not cookies.get("token") or not (cookies.get("userid") or "").isdigit():
            raise ValueError("酷狗未返回可用会话")
        for _ in range(3):
            request(base, "/register/dev", cookies)
            if cookies.get("dfid"):
                break
        if not cookies.get("dfid"):
            raise ValueError("未取得酷狗设备 ID")
        if not request(base, "/user/verify", cookies).get("auth"):
            raise ValueError("酷狗未通过播放会话验证")
        request(base, "/user/vip/detail", cookies)
        save(args.output, cookies)
        print("播放会话已验证并保存。", flush=True)
    except (OSError, ValueError) as exc:
        parser.exit(1, (str(exc) if type(exc) is ValueError else "酷狗连接或本地文件操作失败") + "\n")


if __name__ == "__main__":
    main()
