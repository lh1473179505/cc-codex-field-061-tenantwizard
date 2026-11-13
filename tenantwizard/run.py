"""Multi-tenant wizard runner: isolated sessions, rotating fields, rollback, checkpointing."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from pathlib import Path

from tenantwizard.client import HttpSession
from tenantwizard.parse import checksum, parse_form


def _logical_step(method: str, path: str) -> str:
    if path == "/login":
        return "login"
    if path.startswith("/wizard/"):
        return "wizard-" + path.rsplit("/", 1)[-1]
    if method == "DELETE" and path.startswith("/draft/"):
        return "rollback"
    return "other"


class Journal:
    def __init__(self, path: Path):
        self.path = path

    def log(self, tenant, method, url, status, logical_step, fields, body):
        rec = {
            "tenant": tenant,
            "method": method,
            "url": url,
            "status": status,
            "logical_step": logical_step,
            "request_logical_fields": list(fields),
            "response_sha256": hashlib.sha256(body).hexdigest(),
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _request(session, journal, tid, method, path, data=None, fields=(), query=None):
    actual = path
    if query:
        actual = path + "?" + urllib.parse.urlencode(query)
    resp = session.request(method, actual, data=data)
    journal.log(tid, method, path, resp.status, _logical_step(method, path), fields, resp.body)
    return resp


def _save_checkpoint(path: Path, ckpt: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ckpt, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _payload(form, values):
    data = {"csrf": form["csrf"], "draft_id": form["draft_id"]}
    for logical, value in values.items():
        field = form["by_logical"].get(logical)
        if not field:
            raise RuntimeError(f"page is missing logical field {logical!r}")
        data[field["name"]] = value
    return data


def _delete_draft(session, journal, tid, draft_id, csrf):
    resp = _request(session, journal, tid, "DELETE", f"/draft/{draft_id}", query={"csrf": csrf})
    if resp.status != 200:
        raise RuntimeError(f"{tid}: DELETE /draft/{draft_id} -> {resp.status}")
    return resp


def _wizard_attempt(session, journal, tid, tenant, amount, checksum_amount):
    """Run steps 1-3 once. Yields per HTTP request; returns ("filed", filing) or ("failed", form)."""
    resp = _request(session, journal, tid, "GET", "/wizard/1")
    yield
    if resp.status != 200:
        raise RuntimeError(f"{tid}: GET /wizard/1 -> {resp.status}")
    form = parse_form(resp.text)
    data = _payload(form, {"legal_name": tenant["legal_name"], "tax_id": tenant["tax_id"]})
    resp = _request(session, journal, tid, "POST", "/wizard/1", data=data,
                    fields=("csrf", "draft_id", "legal_name", "tax_id"))
    yield
    if resp.status == 409:
        # Stale open draft from an interrupted run: discard it and restart step 1.
        _delete_draft(session, journal, tid, form["draft_id"], form["csrf"])
        yield
        resp = _request(session, journal, tid, "GET", "/wizard/1")
        yield
        form = parse_form(resp.text)
        data = _payload(form, {"legal_name": tenant["legal_name"], "tax_id": tenant["tax_id"]})
        resp = _request(session, journal, tid, "POST", "/wizard/1", data=data,
                        fields=("csrf", "draft_id", "legal_name", "tax_id"))
        yield
    if resp.status != 302:
        raise RuntimeError(f"{tid}: POST /wizard/1 -> {resp.status}")

    resp = _request(session, journal, tid, "GET", "/wizard/2")
    yield
    if resp.status != 200:
        raise RuntimeError(f"{tid}: GET /wizard/2 -> {resp.status}")
    form = parse_form(resp.text)
    chk = checksum(tenant["tax_id"], tenant["year"], checksum_amount)
    data = _payload(form, {"year": tenant["year"], "amount": amount, "checksum": chk})
    resp = _request(session, journal, tid, "POST", "/wizard/2", data=data,
                    fields=("csrf", "draft_id", "year", "amount", "checksum"))
    yield
    if resp.status == 422:
        return "failed", parse_form(resp.text)
    if resp.status != 302:
        raise RuntimeError(f"{tid}: POST /wizard/2 -> {resp.status}")

    resp = _request(session, journal, tid, "GET", "/wizard/3")
    yield
    if resp.status != 200:
        raise RuntimeError(f"{tid}: GET /wizard/3 -> {resp.status}")
    form = parse_form(resp.text)
    data = _payload(form, {"confirm": "yes"})
    resp = _request(session, journal, tid, "POST", "/wizard/3", data=data,
                    fields=("csrf", "draft_id", "confirm"))
    yield
    if resp.status != 200:
        raise RuntimeError(f"{tid}: POST /wizard/3 -> {resp.status}")
    return "filed", json.loads(resp.text)


def _tenant_flow(tenant, base, journal, ckpt, ckpt_path):
    """Generator driving one tenant to a filed state; yields once per HTTP request."""
    tid = tenant["id"]
    state = ckpt["tenants"].setdefault(tid, {"rollbacks": []})
    session = HttpSession(base)  # fresh jar per tenant: no shared cookies

    resp = _request(session, journal, tid, "GET", "/login")
    yield
    if resp.status != 200:
        raise RuntimeError(f"{tid}: GET /login -> {resp.status}")
    form = parse_form(resp.text)
    resp = _request(session, journal, tid, "POST", "/login",
                    data={"username": tenant["username"], "password": tenant["password"], "csrf": form["csrf"]},
                    fields=("username", "password", "csrf"))
    yield
    if resp.status != 302:
        raise RuntimeError(f"{tid}: POST /login -> {resp.status}")

    if tenant.get("amount_first_try") and state.get("status") != "rolled_back":
        # First attempt uses the wrong amount; checksum is computed over the correct
        # amount so the portal rejects it with 422 (a matching checksum would file
        # the wrong amount for real).
        status, info = yield from _wizard_attempt(
            session, journal, tid, tenant,
            amount=tenant["amount_first_try"], checksum_amount=tenant["amount"])
        if status != "failed":
            raise RuntimeError(f"{tid}: first attempt with wrong amount unexpectedly succeeded")
        _delete_draft(session, journal, tid, info["draft_id"], info["csrf"])
        yield
        state["rollbacks"].append(info["draft_id"])
        state["status"] = "rolled_back"
        _save_checkpoint(ckpt_path, ckpt)

    status, filing = yield from _wizard_attempt(
        session, journal, tid, tenant,
        amount=tenant["amount"], checksum_amount=tenant["amount"])
    if status != "filed":
        raise RuntimeError(f"{tid}: final attempt was rejected")
    state["filing"] = {
        "tenant": tid,
        "filing_id": filing["filing_id"],
        "legal_name": filing["legal_name"],
        "tax_id": filing["tax_id"],
        "year": filing["year"],
        "amount": filing["amount"],
    }
    state["status"] = "filed"
    _save_checkpoint(ckpt_path, ckpt)


def _drive(flows, interleave):
    if not interleave:
        for flow in flows:
            for _ in flow:
                pass
        return
    active = list(flows)
    while active:
        remaining = []
        for flow in active:
            try:
                next(flow)
                remaining.append(flow)
            except StopIteration:
                pass
        active = remaining


def run(args) -> int:
    tenants = json.loads(Path(args.tenants).read_text(encoding="utf-8"))["tenants"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ckpt_path = Path(args.checkpoint)
    if ckpt_path.is_file():
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
    else:
        ckpt = {"tenants": {}}
    journal = Journal(out / "journal.jsonl")

    flows = []
    for tenant in tenants:
        state = ckpt["tenants"].get(tenant["id"], {})
        if state.get("status") == "filed":
            continue  # already filed: do not resubmit
        flows.append(_tenant_flow(tenant, args.base, journal, ckpt, ckpt_path))

    try:
        _drive(flows, interleave=args.interleave)
    except RuntimeError as exc:
        print(f"run failed: {exc}")
        return 1

    filings = sorted(
        (ckpt["tenants"][t["id"]]["filing"] for t in tenants),
        key=lambda row: row["tenant"],
    )
    (out / "filings.json").write_text(
        json.dumps(filings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rollbacks = [
        {"tenant": tid, "discarded_draft_id": draft_id}
        for tid, state in ckpt["tenants"].items()
        for draft_id in state.get("rollbacks", [])
    ]
    if rollbacks:
        (out / "rollbacks.json").write_text(
            json.dumps(rollbacks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"filed {len(filings)} tenant(s); journal at {out / 'journal.jsonl'}")
    return 0
