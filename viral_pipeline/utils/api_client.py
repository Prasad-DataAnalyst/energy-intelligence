"""
Base HTTP client with retry, rate-limiting, and SSL bypass for cloud environments.
"""
import asyncio
import ssl
import time
import logging
from typing import Any, Dict, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

log = logging.getLogger(__name__)

# Global permissive SSL context for cloud environments with MITM proxies
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def make_session(retries: int = 3, backoff: float = 0.5,
                 timeout: int = 30) -> requests.Session:
    """Create a requests.Session with retry and SSL bypass."""
    session = requests.Session()
    session.verify = False  # bypass SSL verification

    retry_cfg = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT"],
    )
    adapter = HTTPAdapter(max_retries=retry_cfg)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "ViralPipeline/1.0 (research bot)"})
    return session


# Singleton session
_SESSION: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = make_session()
    return _SESSION


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def get(url: str, params: Dict = None, headers: Dict = None,
        timeout: int = 20, **kwargs) -> requests.Response:
    """GET with automatic retry on network errors."""
    sess = get_session()
    if headers:
        sess.headers.update(headers)
    resp = sess.get(url, params=params, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def post(url: str, data: Any = None, json: Any = None,
         headers: Dict = None, timeout: int = 60, **kwargs) -> requests.Response:
    """POST with automatic retry on network errors."""
    sess = get_session()
    if headers:
        sess.headers.update(headers)
    resp = sess.post(url, data=data, json=json, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


def download_file(url: str, dest: str, chunk_size: int = 65536) -> str:
    """Download a file with streaming and retry."""
    sess = get_session()
    with sess.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                f.write(chunk)
    return dest


class RateLimiter:
    """Simple token-bucket rate limiter."""
    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
        self._last = 0.0

    def wait(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()

    async def async_wait(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


# Suppress SSL warnings globally (cloud env)
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
