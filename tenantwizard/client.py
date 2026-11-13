"""HTTP helper. Each HttpSession owns its cookie jar, so tenants never share cookies."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 302s to the caller so each HTTP call is explicit (and journalable)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes
    final_url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class HttpSession:
    """Per-tenant session: isolated CookieJar and opener per instance."""

    def __init__(self, base: str, user_agent: str = "tenantwizard/0.1"):
        self.base = base.rstrip("/")
        self.user_agent = user_agent
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            _NoRedirect(),
        )

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
