"""Polite HTTP access: per-thread sessions, retries, per-domain rate limit and robots.txt checks."""

import logging
import threading
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from tenacity import before_sleep_log, retry, retry_if_exception, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; OutbreakMonitorPrototype/0.1)"
TIMEOUT_SECONDS = 20               # per network operation (connect, each read)
MAX_DOWNLOAD_SECONDS = 60          # whole response body; stops servers that trickle data
MAX_DOWNLOAD_BYTES = 20_000_000
SLOW_SECONDS = 10                  # responses slower than this are logged
MIN_SECONDS_PER_DOMAIN = 1.0       # at most one request per second to any single site
ROBOTS_TTL_SECONDS = 24 * 3600     # re-read robots.txt daily
ROBOTS_ERROR_TTL_SECONDS = 600     # if robots.txt could not be read, try again after 10 minutes

_local = threading.local()         # one requests.Session per thread (sources are fetched in parallel)
_domain_locks: dict[str, threading.Lock] = {}
_domain_locks_guard = threading.Lock()
_last_request: dict[str, float] = {}
_robots: dict[str, tuple[RobotFileParser | None, float]] = {}  # site root -> (parser or None, expiry)


class RobotsUnavailable(Exception):
    """robots.txt could not be read (network error or server error); try again later."""


class DownloadTooLarge(requests.RequestException):
    """The response exceeded the time or size limit."""


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers["User-Agent"] = USER_AGENT
    return _local.session


def is_transient(exc: BaseException) -> bool:
    """Worth retrying: network errors, timeouts, HTTP 429 and 5xx. Not 403/404 etc."""
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and (exc.response.status_code == 429 or exc.response.status_code >= 500)
    return isinstance(exc, (requests.ConnectionError, requests.Timeout))


def _throttle(netloc: str) -> None:
    """Wait so that requests to one site are at least MIN_SECONDS_PER_DOMAIN apart (thread-safe)."""
    with _domain_locks_guard:
        lock = _domain_locks.setdefault(netloc, threading.Lock())
    with lock:
        wait = _last_request.get(netloc, 0) + MIN_SECONDS_PER_DOMAIN - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[netloc] = time.monotonic()


@retry(retry=retry_if_exception(is_transient), stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=2, max=30), reraise=True,
       before_sleep=before_sleep_log(log, logging.WARNING))
def get(url: str) -> requests.Response:
    """GET with rate limiting and a limit on total download time/size.
    Retries transient failures; raises on HTTP errors."""
    _throttle(urlsplit(url).netloc)
    start = time.monotonic()
    with _session().get(url, timeout=TIMEOUT_SECONDS, stream=True) as resp:
        resp.raise_for_status()
        body, size = [], 0
        for chunk in resp.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES or time.monotonic() - start > MAX_DOWNLOAD_SECONDS:
                raise DownloadTooLarge(f"response over {MAX_DOWNLOAD_SECONDS}s or {MAX_DOWNLOAD_BYTES} bytes: {url}")
            body.append(chunk)
        resp._content = b"".join(body)  # make .content/.text available after streaming
    elapsed = time.monotonic() - start
    if elapsed > SLOW_SECONDS:
        log.warning("Slow response (%.0fs): %s", elapsed, url)
    return resp


def _load_robots(root: str) -> RobotFileParser | None:
    """Parsed robots.txt, an allow-all parser if the site has none (4xx), or None if unreadable."""
    try:
        _throttle(urlsplit(root).netloc)
        resp = _session().get(root + "/robots.txt", timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        return None
    if resp.status_code >= 500:
        return None
    parser = RobotFileParser()
    if resp.status_code >= 400:
        parser.allow_all = True
    else:
        parser.parse(resp.text.splitlines())
    return parser


def allowed_by_robots(url: str) -> bool:
    """Whether robots.txt allows fetching url (cached 24 h per site).
    Raises RobotsUnavailable if robots.txt cannot be read; that result is cached for 10 minutes."""
    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}"
    cached = _robots.get(root)
    if cached is None or cached[1] < time.monotonic():
        parser = _load_robots(root)
        ttl = ROBOTS_TTL_SECONDS if parser else ROBOTS_ERROR_TTL_SECONDS
        cached = _robots[root] = (parser, time.monotonic() + ttl)
    if cached[0] is None:
        raise RobotsUnavailable(f"robots.txt unavailable for {root}")
    return cached[0].can_fetch(USER_AGENT, url)
