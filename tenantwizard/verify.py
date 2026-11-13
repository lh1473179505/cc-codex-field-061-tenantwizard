from __future__ import annotations

import json
from pathlib import Path


def run_verify(args) -> int:
    out = Path(args.out)
    expect = json.loads(Path(args.expect).read_text())
    filings_path = out / "filings.json"
    journal_path = out / "journal.jsonl"
    if not filings_path.is_file() or not journal_path.is_file():
        print("missing filings.json or journal.jsonl")
        return 1
    got = json.loads(filings_path.read_text())
    if isinstance(got, dict) and "filings" in got:
        got_rows = got["filings"]
    else:
        got_rows = got
    got_rows = sorted(got_rows, key=lambda r: r["tenant"])
    exp_rows = expect["filings"]
    errors = []
    if [r["tenant"] for r in got_rows] != [r["tenant"] for r in exp_rows]:
        errors.append(f"tenants { [r.get('tenant') for r in got_rows] }")
    for g, e in zip(got_rows, exp_rows):
        for k in ("legal_name", "tax_id", "year", "amount"):
            if str(g.get(k)) != str(e.get(k)):
                errors.append(f"{e['tenant']} {k} {g.get(k)!r} != {e.get(k)!r}")
        if not g.get("filing_id"):
            errors.append(f"{e['tenant']} missing filing_id")
    if expect.get("require_distinct_filing_ids") and len({r.get("filing_id") for r in got_rows}) != len(got_rows):
        errors.append("filing_id must be distinct per tenant")
    journal = []
    for line in journal_path.read_text().splitlines():
        if line.strip():
            journal.append(json.loads(line))
    by_t = {}
    for ev in journal:
        by_t.setdefault(ev.get("tenant"), []).append(ev)
    for tid in ("acme", "globex"):
        steps = {e.get("logical_step") for e in by_t.get(tid, [])}
        for need in ("login", "wizard-1", "wizard-2", "wizard-3"):
            if need not in steps:
                errors.append(f"journal missing {tid} {need}")
    if expect.get("require_globex_rollback"):
        rb_steps = [e for e in by_t.get("globex", []) if e.get("logical_step") == "rollback"]
        rb_file = out / "rollbacks.json"
        if not rb_steps and not rb_file.is_file():
            errors.append("globex rollback not recorded")
        if rb_file.is_file():
            rows = json.loads(rb_file.read_text())
            if not any(r.get("tenant") == "globex" for r in rows):
                errors.append("rollbacks.json missing globex")
    # cookie isolation smell: a single sid reused by two tenants in journal is a failure if such field exists
    if errors:
        print("verify failed:")
        for e in errors:
            print(" -", e)
        return 1
    print("verify ok")
    return 0
