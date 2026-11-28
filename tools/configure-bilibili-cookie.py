#!/usr/bin/env python3
"""Set the Bilibili service session from a Bilibili web QR login."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile

from bilibili_api import login_v2, user
from bilibili_api.utils.network import HEADERS


def write_session(path, credential):
    values = {key: getattr(credential, key, None) for key in (
        'sessdata', 'bili_jct', 'dedeuserid', 'buvid3', 'buvid4')}
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix=f'.{path.name}-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump({key: value for key, value in values.items() if value}, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


async def configure(target, image):
    client = login_v2.get_client()
    request = client.request

    async def passport_request(*args, **kwargs):
        if kwargs.get('url', '').startswith('https://passport.bilibili.com/'):
            kwargs['headers'] = {**HEADERS, **(kwargs.get('headers') or {})}
        return await request(*args, **kwargs)

    client.request = passport_request
    qr = login_v2.QrCodeLogin()
    await asyncio.wait_for(qr.generate_qrcode(), 15)
    if await asyncio.wait_for(qr.check_state(), 15) != login_v2.QrCodeLoginEvents.SCAN:
        raise RuntimeError('二维码状态异常')
    if image:
        image.write_bytes(qr.get_qrcode_picture().content)
        print('qr_ready', flush=True)
    else:
        print(qr.get_qrcode_terminal(), flush=True)
    deadline = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < deadline:
        state = await asyncio.wait_for(qr.check_state(), 12)
        if state == login_v2.QrCodeLoginEvents.DONE:
            credential = qr.get_credential()
            account = await asyncio.wait_for(user.get_self_info(credential), 15)
            if not credential.has_sessdata() or (account.get('vip') or {}).get('status') != 1:
                raise RuntimeError('大会员会话验证失败')
            write_session(target, credential)
            print('已验证大会员并保存服务端会话。', flush=True)
            return
        if state == login_v2.QrCodeLoginEvents.TIMEOUT:
            break
        await asyncio.sleep(3)
    raise TimeoutError('二维码已过期')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-file', type=Path, default=Path('/etc/currentmusic/bilibili-cookie'))
    parser.add_argument('--qr-image', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        asyncio.run(configure(args.session_file, args.qr_image))
    except Exception as error:
        parser.exit(1, f'登录配置失败（{type(error).__name__}）\n')
    finally:
        if args.qr_image:
            args.qr_image.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
