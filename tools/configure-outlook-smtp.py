#!/usr/bin/env python3
"""Authorize personal Outlook.com SMTP using Microsoft's device login."""
import argparse
from pathlib import Path
import sys
import time
from urllib.error import URLError
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from cm_mail_outlook import OutlookOAuthError, SCOPE, request_oauth, save_credentials


def authorize(client_id):
    device = request_oauth("devicecode", {"client_id": client_id, "scope": SCOPE})
    print(f"打开 {device['verification_uri']}，输入设备登录码 {device['user_code']}。", flush=True)
    fields = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": client_id,
        "device_code": device["device_code"],
    }
    interval = device["interval"]
    deadline = time.monotonic() + device["expires_in"]
    while time.monotonic() + interval < deadline:
        time.sleep(interval)
        try:
            return request_oauth("token", fields)["refresh_token"]
        except OutlookOAuthError as exc:
            if exc.code == "slow_down":
                interval += 5
            elif exc.code != "authorization_pending":
                raise
        except URLError:
            interval += 5
    raise OutlookOAuthError("expired_token")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_id", type=UUID)
    parser.add_argument("credential_file", type=Path)
    args = parser.parse_args()
    if not args.credential_file.parent.is_dir():
        parser.error("授权文件的父目录必须已存在")

    client_id = str(args.client_id)
    try:
        refresh_token = authorize(client_id)
        save_credentials(args.credential_file, client_id, refresh_token)
    except OutlookOAuthError as exc:
        messages = {
            "authorization_declined": "用户拒绝了授权",
            "expired_token": "设备登录码已过期，请重新运行",
            "invalid_client": "应用配置不支持 Outlook 设备登录",
        }
        parser.exit(1, messages.get(exc.code, "微软未授予 SMTP.Send 授权") + "\n")
    except (OSError, KeyError, ValueError):
        parser.exit(1, "无法完成 Outlook 授权或保存授权文件\n")
    print(f"授权已保存到 {args.credential_file}，权限为 0600。")


if __name__ == "__main__":
    main()
