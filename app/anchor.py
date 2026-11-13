"""Fingerprint a period's books and anchor / verify them on NEAR testnet.

books_root  = sha256 over the ordered list of per-transaction leaf hashes
report_hash = sha256 of the canonical JSON of the period's statements
Only these two hashes and a count go on chain. Signing is done by near/anchor.mjs
(near-api-js) with a function-call key that can only call `anchor`.
"""
import base64
import hashlib
import json
import os
import subprocess

import requests

from . import db, ledger
from .config import settings

LEAF_FIELDS = ("id", "fingerprint", "reference", "occurred_at", "amount_minor", "currency", "direction", "category")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprints(period: str) -> dict:
    where, args = ("", ()) if period == "all" else (" WHERE substr(occurred_at,1,7) = ?", (period,))
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM transactions{where} ORDER BY id", args)]
        stmts = ledger.statements(c, period)
    leaves = [hashlib.sha256(canonical({k: r[k] for k in LEAF_FIELDS})).hexdigest() for r in rows]
    root = hashlib.sha256("".join(leaves).encode()).hexdigest()
    report = hashlib.sha256(canonical(stmts)).hexdigest()
    return {"period": period, "books_root": root, "report_hash": report, "tx_count": len(rows)}


def _view(method: str, args: dict):
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "query",
        "params": {"request_type": "call_function", "finality": "final", "account_id": settings.books_contract,
                   "method_name": method, "args_base64": base64.b64encode(canonical(args)).decode()},
    }
    r = requests.post(settings.near_rpc, json=body, timeout=20).json()
    if "error" in r or "error" in r.get("result", {}):
        raise RuntimeError(str(r.get("error") or r["result"]["error"])[:300])
    return json.loads(bytes(r["result"]["result"]).decode() or "null")


def anchor(period: str) -> dict:
    if not settings.books_contract:
        raise RuntimeError("BOOKS_CONTRACT_ID is not set; run the NEAR setup first (see README)")
    fp = fingerprints(period)
    script = os.path.join(os.path.dirname(__file__), "..", "near", "anchor.mjs")
    out = subprocess.run(
        ["node", script, settings.books_contract, fp["period"], fp["books_root"], fp["report_hash"], str(fp["tx_count"])],
        capture_output=True, text=True, timeout=170,
    )
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip()[-400:])
    res = json.loads(out.stdout.strip().splitlines()[-1])
    with db.conn() as c:
        c.execute(
            "INSERT INTO anchors(period, books_root, report_hash, tx_count, near_tx, contract) VALUES (?,?,?,?,?,?)",
            (fp["period"], fp["books_root"], fp["report_hash"], fp["tx_count"], res["tx"], settings.books_contract),
        )
    return {**fp, "near_tx": res["tx"], "contract": settings.books_contract, "explorer": f"https://testnet.nearblocks.io/txns/{res['tx']}"}


def verify(period: str) -> dict:
    fp = fingerprints(period)
    if not settings.books_contract:
        return {**fp, "anchored": None, "match": False, "detail": "BOOKS_CONTRACT_ID not set"}
    on_chain = _view("get_latest", {"period": period})
    match = bool(on_chain) and on_chain["books_root"] == fp["books_root"] and on_chain["report_hash"] == fp["report_hash"]
    detail = (
        "books match what was anchored on NEAR" if match
        else "nothing anchored for this period yet" if not on_chain
        else "books changed since they were anchored"
    )
    return {**fp, "anchored": on_chain, "match": match, "detail": detail, "contract": settings.books_contract}
