"""HTTP helper. WARNING: cookie jar is process-global, which leaks across tenants."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar


# Existing code shares one jar. Concurrent or sequential multi-tenant runs will leak.
SHARED_JAR = CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(SHARED_JAR))


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes
    final_url: str


class HttpSession:
    """Thin wrapper around the shared opener. Isolated sessions are not implemented."""

    def __init__(self, base: str, user_agent: str = "tenantwizard/0.1"):
        self.base = base.rstrip("/")
        self.user_agent = user_agent
        self.opener = _OPENER  # shared on purpose (current bug)

    def request(self, method: str, path: str, data: dict | None = None, headers: dict | None = None) -> Response:
        url = path if path.startswith("http") else self.base + path
        hdrs = {"User-Agent": self.user_agent}
        if headers:
            hdrs.update(headers)
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=10) as r:
                return Response(url=url, status=r.getcode(), headers={k.lower(): v for k, v in r.headers.items()}, body=r.read(), final_url=r.geturl())
        except urllib.error.HTTPError as e:
            return Response(url=url, status=e.code, headers={k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, body=e.read() if e.fp else b"", final_url=url)
