"""Local multi-tenant filing wizard. Field names rotate per session."""
from __future__ import annotations

import hashlib
import json
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


USERS = {
    "acme.bot": {"password": "acme-fixture-only", "tenant": "acme"},
    "globex.bot": {"password": "globex-fixture-only", "tenant": "globex"},
}

SESSIONS = {}  # sid -> dict
DRAFTS = {}
FILINGS = {}
LOGIN_CSRF = {}  # token -> unused login csrf


def _hash_name(sid: str, step: int, logical: str) -> str:
    return "f_" + hashlib.sha256(f"{sid}:{step}:{logical}".encode()).hexdigest()[:10]


def _new_csrf() -> str:
    return secrets.token_hex(8)


def _html_form(title, action, fields, extra=""):
    inputs = []
    for f in fields:
        typ = f.get("type", "text")
        logical = f'data-logical="{f["logical"]}"' if f.get("logical") else ""
        inputs.append(
            f'<label>{f.get("label") or f.get("logical") or f["name"]}'
            f'<input type="{typ}" name="{f["name"]}" value="{f.get("value","")}" {logical}></label>'
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>"
        + title
        + "</title></head><body><h1>"
        + title
        + "</h1><form method='post' action='"
        + action
        + "'>"
        + "".join(inputs)
        + "<button type='submit'>go</button></form>"
        + extra
        + "</body></html>"
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _sid(self):
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        if "tw_sid" in c:
            return c["tw_sid"].value
        return None

    def _send(self, status, body, content_type="text/html; charset=utf-8", set_cookie=None, extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_form(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return {k: v[0] for k, v in parse_qs(raw.decode(), keep_blank_values=True).items()}

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/login":
            csrf = _new_csrf()
            LOGIN_CSRF[csrf] = True
            html = _html_form("login", "/login", [
                {"name": "username", "logical": "username", "label": "user"},
                {"name": "password", "type": "password", "logical": "password", "label": "pass"},
                {"name": "csrf", "type": "hidden", "logical": "csrf", "value": csrf},
            ])
            return self._send(200, html)
        if path.startswith("/draft/"):
            sid = self._sid()
            sess = SESSIONS.get(sid)
            if not sess:
                return self._send(401, "no session")
            did = path.split("/")[-1]
            d = DRAFTS.get(did)
            if not d or d["tenant"] != sess["tenant"]:
                return self._send(404, "no draft")
            html = _html_form("draft", "/wizard/" + str(d["step"]), [
                {"name": "csrf", "type": "hidden", "logical": "csrf", "value": sess["csrf"]},
                {"name": "draft_id", "type": "hidden", "logical": "draft_id", "value": did},
            ])
            return self._send(200, html)
        if path.startswith("/wizard/"):
            return self._wizard_get(int(path.rsplit("/", 1)[-1]))
        return self._send(404, "not found")

    def _wizard_get(self, step: int):
        sid = self._sid()
        sess = SESSIONS.get(sid)
        if not sess:
            return self._send(401, "no session")
        sess["csrf"] = _new_csrf()
        draft = None
        for d in DRAFTS.values():
            if d["tenant"] == sess["tenant"] and d["status"] == "open":
                draft = d
                break
        if draft is None:
            did = secrets.token_hex(6)
            draft = {"id": did, "tenant": sess["tenant"], "step": 1, "status": "open", "data": {}}
            DRAFTS[did] = draft
            sess["draft_id"] = did
        fields = [
            {"name": "csrf", "type": "hidden", "logical": "csrf", "value": sess["csrf"]},
            {"name": "draft_id", "type": "hidden", "logical": "draft_id", "value": draft["id"]},
        ]
        if step == 1:
            for logical in ("legal_name", "tax_id"):
                fields.append({"name": _hash_name(sid, 1, logical), "logical": logical, "value": draft["data"].get(logical, "")})
        elif step == 2:
            for logical in ("year", "amount", "checksum"):
                fields.append({"name": _hash_name(sid, 2, logical), "logical": logical, "value": draft["data"].get(logical, "")})
        elif step == 3:
            fields.append({"name": _hash_name(sid, 3, "confirm"), "logical": "confirm", "value": ""})
        else:
            return self._send(404, "no step")
        return self._send(200, _html_form(f"wizard {step}", f"/wizard/{step}", fields))

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        form = self._read_form()
        if path == "/login":
            csrf = form.get("csrf")
            if not csrf or not LOGIN_CSRF.pop(csrf, None):
                return self._send(400, "bad csrf")
            user = USERS.get(form.get("username") or "")
            if not user or user["password"] != form.get("password"):
                return self._send(401, "bad login")
            sid = secrets.token_hex(12)
            SESSIONS[sid] = {"tenant": user["tenant"], "username": form.get("username"), "csrf": _new_csrf()}
            return self._send(302, "", set_cookie=f"tw_sid={sid}; Path=/; HttpOnly", extra={"Location": "/wizard/1"})
        if path.startswith("/wizard/"):
            return self._wizard_post(int(path.rsplit("/", 1)[-1]), form)
        return self._send(404, "not found")

    def _wizard_post(self, step, form):
        sid = self._sid()
        sess = SESSIONS.get(sid)
        if not sess:
            return self._send(401, "no session")
        if form.get("csrf") != sess.get("csrf"):
            return self._send(403, "bad csrf")
        did = form.get("draft_id")
        draft = DRAFTS.get(did)
        if not draft or draft["tenant"] != sess["tenant"]:
            return self._send(404, "no draft")
        if draft["status"] != "open":
            return self._send(409, "draft closed")
        def val(logical):
            return form.get(_hash_name(sid, step, logical), "")
        if step == 1:
            if draft["step"] != 1:
                return self._send(409, "draft already advanced; delete it before restarting step 1")
            name, tax = val("legal_name"), val("tax_id")
            if not name or not tax:
                sess["csrf"] = _new_csrf()
                return self._send(422, _html_form("wizard 1", "/wizard/1", [
                    {"name": "csrf", "type": "hidden", "logical": "csrf", "value": sess["csrf"]},
                    {"name": "draft_id", "type": "hidden", "logical": "draft_id", "value": did},
                    {"name": _hash_name(sid, 1, "legal_name"), "logical": "legal_name", "value": name},
                    {"name": _hash_name(sid, 1, "tax_id"), "logical": "tax_id", "value": tax},
                ], extra="<p>need name and tax id</p>"))
            draft["data"]["legal_name"] = name
            draft["data"]["tax_id"] = tax
            draft["step"] = 2
            sess["csrf"] = _new_csrf()
            return self._send(302, "", extra={"Location": "/wizard/2"})
        if step == 2:
            year, amount, chk = val("year"), val("amount"), val("checksum")
            expect = hashlib.sha256(f"{draft['data'].get('tax_id','')}:{year}:{amount}".encode()).hexdigest()[:6]
            if chk != expect or not year or not amount:
                sess["csrf"] = _new_csrf()
                return self._send(422, _html_form("wizard 2", "/wizard/2", [
                    {"name": "csrf", "type": "hidden", "logical": "csrf", "value": sess["csrf"]},
                    {"name": "draft_id", "type": "hidden", "logical": "draft_id", "value": did},
                    {"name": _hash_name(sid, 2, "year"), "logical": "year", "value": year},
                    {"name": _hash_name(sid, 2, "amount"), "logical": "amount", "value": amount},
                    {"name": _hash_name(sid, 2, "checksum"), "logical": "checksum", "value": ""},
                ], extra="<p>checksum mismatch</p>"))
            draft["data"]["year"] = year
            draft["data"]["amount"] = amount
            draft["step"] = 3
            sess["csrf"] = _new_csrf()
            return self._send(302, "", extra={"Location": "/wizard/3"})
        if step == 3:
            if val("confirm") != "yes":
                sess["csrf"] = _new_csrf()
                return self._send(422, _html_form("wizard 3", "/wizard/3", [
                    {"name": "csrf", "type": "hidden", "logical": "csrf", "value": sess["csrf"]},
                    {"name": "draft_id", "type": "hidden", "logical": "draft_id", "value": did},
                    {"name": _hash_name(sid, 3, "confirm"), "logical": "confirm", "value": ""},
                ], extra="<p>confirm must be yes</p>"))
            fid = "FIL-" + secrets.token_hex(4)
            draft["status"] = "filed"
            FILINGS[fid] = {"filing_id": fid, "tenant": draft["tenant"], **draft["data"]}
            sess["csrf"] = _new_csrf()
            return self._send(200, json.dumps(FILINGS[fid], ensure_ascii=False), content_type="application/json")
        return self._send(404, "no step")

    def do_DELETE(self):
        path = unquote(urlparse(self.path).path)
        if not path.startswith("/draft/"):
            return self._send(404, "not found")
        sid = self._sid()
        sess = SESSIONS.get(sid)
        if not sess:
            return self._send(401, "no session")
        # csrf via query or header
        qs = parse_qs(urlparse(self.path).query)
        csrf = qs.get("csrf", [None])[0] or self.headers.get("X-CSRF")
        if csrf != sess.get("csrf"):
            return self._send(403, "bad csrf")
        did = path.split("/")[-1]
        draft = DRAFTS.get(did)
        if not draft or draft["tenant"] != sess["tenant"]:
            return self._send(404, "no draft")
        if draft["status"] != "open":
            return self._send(409, "cannot delete filed draft")
        del DRAFTS[did]
        return self._send(200, json.dumps({"deleted": did}), content_type="application/json")


def serve(host="127.0.0.1", port=8762):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"tenantwizard fixture site on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
