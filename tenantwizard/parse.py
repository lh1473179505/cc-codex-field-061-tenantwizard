"""Helpers to read csrf, draft_id and logical field names from portal HTML."""
from __future__ import annotations

import re
from html.parser import HTMLParser


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = []  # dicts: name, value, logical, type

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        d = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if not d.get("name"):
            return
        self.fields.append({
            "name": d.get("name"),
            "value": d.get("value") or "",
            "logical": d.get("data-logical") or "",
            "type": d.get("type") or "text",
        })


def parse_form(html: str) -> dict:
    p = _FormParser()
    p.feed(html)
    by_logical = {f["logical"]: f for f in p.fields if f["logical"]}
    by_name = {f["name"]: f for f in p.fields}
    csrf = by_name.get("csrf", {}).get("value") or by_logical.get("csrf", {}).get("value")
    draft = by_name.get("draft_id", {}).get("value") or by_logical.get("draft_id", {}).get("value")
    return {"fields": p.fields, "by_logical": by_logical, "csrf": csrf, "draft_id": draft}


def checksum(tax_id: str, year: str, amount: str) -> str:
    import hashlib
    return hashlib.sha256(f"{tax_id}:{year}:{amount}".encode()).hexdigest()[:6]
