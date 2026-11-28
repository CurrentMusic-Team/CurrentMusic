"""Shared, short-lived mirror of public CurrentMusicX CI releases."""

from contextlib import closing
import http.client
import json
import logging
import os
import re
from threading import Lock
import time


_PATH = "/repos/bileizhen/CurrentMusicX/releases?per_page=100"
_CACHE_SECONDS = 60
_STALE_SECONDS = 3600
_MAX_BYTES = 4 * 1024 * 1024
_CI_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+-ci\.[1-9][0-9]*")
log = logging.getLogger(__name__)


def _retry_delay(response):
    delay = _CACHE_SECONDS
    retry_after = response.getheader("Retry-After") or ""
    if re.fullmatch(r"[0-9]{1,9}", retry_after):
        delay = max(delay, int(retry_after))
    if response.getheader("X-RateLimit-Remaining") == "0":
        reset = response.getheader("X-RateLimit-Reset") or ""
        if re.fullmatch(r"[0-9]{1,12}", reset):
            delay = max(delay, int(reset) - time.time() + 1)
    return delay


class CIReleaseCache:
    def __init__(self):
        self._lock = Lock()
        self._releases = None
        self._etag = None
        self._refresh_at = 0
        self._stale_until = 0

    def get(self):
        # The backend uses one process with several request threads. Holding the
        # lock through refresh also shares upstream failures among waiting clients.
        with self._lock:
            now = time.monotonic()
            if now >= self._refresh_at:
                try:
                    refreshed, delay = self._refresh()
                except (OSError, http.client.HTTPException, ValueError, RecursionError) as exc:
                    refreshed, delay = False, _CACHE_SECONDS
                    log.warning("CurrentMusicX CI release refresh failed (%s)", type(exc).__name__)
                now = time.monotonic()
                self._refresh_at = now + delay
                if refreshed:
                    self._stale_until = now + _STALE_SECONDS
            return self._releases if now < self._stale_until else None

    def _refresh(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "CurrentMusic-X-Releases",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("CM_X_RELEASES_GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = "Bearer " + token
        if self._etag:
            headers["If-None-Match"] = self._etag
        with closing(http.client.HTTPSConnection("api.github.com", timeout=5)) as conn:
            conn.request("GET", _PATH, headers=headers)
            response = conn.getresponse()
            if response.status == 304 and self._releases is not None:
                return True, _CACHE_SECONDS
            if response.status != 200:
                log.warning("CurrentMusicX CI release refresh returned HTTP %s", response.status)
                return False, _retry_delay(response)
            data = response.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise ValueError("release response too large")
            releases = json.loads(data)
            if not isinstance(releases, list) or any(not isinstance(r, dict) for r in releases):
                raise ValueError("release response is not a list of objects")
            self._releases = [
                release for release in releases
                if release.get("draft") is False and release.get("prerelease") is True
                and isinstance(release.get("tag_name"), str) and _CI_TAG.fullmatch(release["tag_name"])
            ]
            self._etag = response.getheader("ETag")
            return True, _CACHE_SECONDS


ci_releases = CIReleaseCache()
