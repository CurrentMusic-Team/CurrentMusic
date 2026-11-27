#!/usr/bin/env python3
"""按访问日志回填用户的 IP 属地（一次性/可重跑）。

问题：IP 属地是在「登录/鉴权请求」时才采集的，上线前的老用户在下一次活跃前
都没有属地。而 nginx 的 `POST /cm/plays/<歌曲id>` 日志里有 IP，播放表里有
「用户 + 歌曲 + 时间」，两边可以精确对上。

匹配规则（宁缺毋滥，**歧义一律跳过**）：
  1. 取 `POST /cm/plays/<ncm_id>` 的日志行，键 = (ncm_id, 秒级时间, IP)；
     只有「同一键下唯一 IP」的才参与匹配；
  2. 播放表里同一 (ncm_id, played_at) 若对应多个用户 → 跳过（无法确定是谁）；
  3. 同一用户取最近一秒的唯一可用地址。已有属地由线上请求刷新。

用法：
    python3 tools/backfill-region.py [--log /www/wwwlogs/music.20110208.xyz.log] [--dry]
    python3 tools/backfill-region.py --dry          # 只统计，不写库
"""
from __future__ import annotations

import argparse
import collections
import datetime
from functools import lru_cache
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import cm_db          # noqa: E402
import cm_region as R  # noqa: E402
from cm_client_ip import parse_ip  # noqa: E402

DEFAULT_LOG = "/www/wwwlogs/music.20110208.xyz.log"
LINE_RE = re.compile(
    r'^(?P<ip>\S+)\s+-\s+-\s+\[(?P<ts>[^\]]+)\]\s+"POST /cm/plays/(?P<nid>\d+)'
    r'\s+HTTP/[^"\s]+"\s+2\d{2}\s')


@lru_cache(maxsize=4096)
def _log_timestamp(value):
    return int(datetime.datetime.strptime(value, "%d/%b/%Y:%H:%M:%S %z").timestamp())


def log_ips(path):
    """(ncm_id, 秒级时间) -> 唯一 IP；同一键多 IP 的丢弃（歧义）。"""
    seen = collections.defaultdict(set)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "/cm/plays/" not in line:
                continue
            m = LINE_RE.match(line)
            if not m:
                continue
            try:
                ts = _log_timestamp(m.group("ts"))
            except ValueError:
                continue
            ip = parse_ip(m.group("ip"))
            seen[(int(m.group("nid")), ts)].add(ip)
    unique = {key: next(iter(values)) for key, values in seen.items() if len(values) == 1}
    return {key: str(address) for key, address in unique.items() if R.prefix_of(address)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--dry", action="store_true", help="只统计匹配，不写库")
    ap.add_argument("--limit", type=int, default=200, help="最多回填多少用户")
    args = ap.parse_args()

    if not os.path.isfile(args.log):
        print(f"日志不存在：{args.log}")
        return 1

    ip_map = log_ips(args.log)
    print(f"可用的 (歌曲, 秒) → IP 条目：{len(ip_map)}")

    conn = sqlite3.connect(Path(cm_db.DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT p.user_id, p.ncm_id, p.played_at,
                      (u.username!='__internal__' AND u.disabled=0 AND coalesce(u.ip_region,'')='') AS eligible
               FROM plays p JOIN users u ON u.id=p.user_id
               ORDER BY p.played_at DESC""").fetchall()
    finally:
        conn.close()

    # 同一 (歌曲, 秒) 对应多个用户 → 无法确定，整键跳过
    key_users = collections.defaultdict(set)
    for r in rows:
        key_users[(int(r["ncm_id"]), int(r["played_at"]))].add(int(r["user_id"]))
    ambiguous = {k for k, us in key_users.items() if len(us) > 1}

    hits = {}  # uid -> (最近一次匹配的播放时间, 同一秒的地址集合)
    for r in rows:
        uid = int(r["user_id"])
        if not r["eligible"]:
            continue
        stamp = int(r["played_at"])
        key = (int(r["ncm_id"]), stamp)
        if key in ambiguous or key not in ip_map:
            continue
        latest, addresses = hits.setdefault(uid, (stamp, set()))
        if stamp == latest:  # SQL 已按时间倒序
            addresses.add(ip_map[key])

    candidates = {uid: next(iter(addresses)) for uid, (_, addresses) in hits.items() if len(addresses) == 1}
    ambiguous_users = len(hits) - len(candidates)
    print(f"可回填用户：{len(candidates)}（歧义跳过 {len(ambiguous)} 个播放键和 {ambiguous_users} 个用户）")
    todo = sorted(candidates)[:max(0, args.limit)]
    if args.dry:
        print(f"预演完成：本次最多回填 {len(todo)} 个用户")
        return 0

    done = failed = skipped = 0
    for uid in todo:
        ip = candidates[uid]
        prefix = R.prefix_of(ip)
        region = R._cached(prefix)
        if not region:
            region = R._lookup(ip)
            time.sleep(1.7)                              # 与线上队列同速，避开免费额度
            if region:
                R._remember(prefix, region)
        if not region:
            failed += 1
            continue
        conn = cm_db.connect()
        try:
            with conn:
                changed = conn.execute(
                    "UPDATE users SET ip_region=? WHERE id=? AND disabled=0 AND coalesce(ip_region,'')=''",
                    (region[:32], uid)).rowcount
        finally:
            conn.close()
        done += changed
        skipped += 1 - changed
    print(f"完成：更新 {done}，解析失败 {failed}，已有属地 {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
