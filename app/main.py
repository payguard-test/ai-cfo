"""AI CFO API and dashboard.  Run: uvicorn app.main:app --reload  then open http://127.0.0.1:8000"""
import json
import os
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import anchor, db, ledger, service
from .categories import CATEGORIES
from .config import settings
from .parser import ParseError

db.init()
app = FastAPI(title="AI CFO", version="1.0.0", description="Bank alerts in, classified double-entry books, statements and Telegram updates out.")
STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/brand", StaticFiles(directory=os.path.join(STATIC, "brand")), name="brand")


_LAST: dict[str, float] = {}


def _throttle(key: str, seconds: float) -> None:
    """On the hosted public demo, stop anyone hammering the paid or on-chain actions."""
    if not settings.public_demo:
        return
    now = time.time()
    if now - _LAST.get(key, 0) < seconds:
        raise HTTPException(429, f"Please wait {int(seconds - (now - _LAST[key])) + 1}s and try again (shared public demo).")
    _LAST[key] = now


def _room() -> None:
    if settings.public_demo:
        with db.conn() as c:
            n = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        if n >= settings.max_tx:
            raise HTTPException(429, "The demo books are full. Click Start over to wipe them and run it again.")


class EmailIn(BaseModel):
    subject: str = ""
    body: str
    sender: str = ""


class Reclass(BaseModel):
    category: str


def _require_key(key: str) -> None:
    if settings.ingest_api_key and key != settings.ingest_api_key:
        raise HTTPException(401, "invalid X-Ingest-Key")


def _out(row: dict) -> dict:
    r = dict(row)
    r["amount"] = r["amount_minor"] / 100
    r["category_label"] = CATEGORIES[r["category"]].label if r["category"] in CATEGORIES else r["category"]
    try:
        proof = json.loads(r["ai_proof"]) if r.get("ai_proof") else None
    except json.JSONDecodeError:
        proof = None
    r["ai_proof"] = proof
    r["ai_proof_status"] = proof.get("status") if proof else ""
    return r


@app.get("/")
def home():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/config")
def config():
    return {
        "business": settings.business_name,
        "currency": settings.currency,
        "ai": {"enabled": bool(settings.near_ai_key), "model": settings.near_ai_model, "provider": "NEAR AI Cloud (private inference)"},
        "telegram": bool(settings.telegram_token and settings.telegram_chat_id),
        "inbox": bool(settings.imap_user and settings.imap_password),
        "near": {"contract": settings.books_contract, "network": settings.near_network},
        "public": settings.public_demo,
        "categories": [{"key": c.key, "label": c.label, "direction": c.direction} for c in CATEGORIES.values()],
    }


@app.post("/api/ingest/email")
def ingest(p: EmailIn, x_ingest_key: str = Header(default="")):
    _require_key(x_ingest_key)
    try:
        return _out(service.ingest_email(p.subject, p.body, p.sender, source="api"))
    except ParseError as e:
        raise HTTPException(422, f"could not read this alert: {e}")
    except service.Duplicate as e:
        raise HTTPException(409, str(e))


@app.post("/api/demo/sample/{index}")
def sample(index: int):
    """Feed one of the bundled sample alerts (samples/alerts.json) through the agent."""
    samples = json.load(open(os.path.join(os.path.dirname(__file__), "..", "samples", "alerts.json"), encoding="utf8"))
    if not 0 <= index < len(samples):
        raise HTTPException(404, f"there are {len(samples)} samples")
    s = samples[index]
    _room()
    try:
        return _out(service.ingest_email(s["subject"], s["body"], s["sender"], source="sample"))
    except service.Duplicate as e:
        raise HTTPException(409, str(e))


@app.post("/api/demo/paste")
def paste(p: EmailIn):
    """Dashboard 'paste an alert' box. Local demo only: put the dashboard behind auth in production."""
    if settings.public_demo:
        if len(p.body) > 4000:
            raise HTTPException(413, "alert too long")
        _room()
        _throttle("paste", 5)
    try:
        return _out(service.ingest_email(p.subject, p.body, p.sender, source="pasted"))
    except ParseError as e:
        raise HTTPException(422, f"could not read this alert: {e}")
    except service.Duplicate as e:
        raise HTTPException(409, str(e))


@app.post("/api/demo/reset")
def reset():
    """Start over: wipe the demo books (transactions and journal). Anchors already on NEAR stay on NEAR."""
    _throttle("reset", 10)
    with db.conn() as c:
        c.execute("DELETE FROM journal")
        c.execute("DELETE FROM transactions")
        c.execute("DELETE FROM seen_messages")
    return {"ok": True}


@app.get("/api/demo/samples")
def samples():
    s = json.load(open(os.path.join(os.path.dirname(__file__), "..", "samples", "alerts.json"), encoding="utf8"))
    return [{"index": i, "subject": x["subject"], "sender": x["sender"], "body": x["body"]} for i, x in enumerate(s)]


@app.get("/api/transactions")
def transactions(status: str | None = None):
    q, args = "SELECT * FROM transactions", ()
    if status:
        q, args = q + " WHERE status = ?", (status,)
    with db.conn() as c:
        return [_out(dict(r)) for r in c.execute(q + " ORDER BY occurred_at DESC, id DESC LIMIT 500", args)]


@app.get("/api/transactions/{tx_id}")
def transaction(tx_id: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not r:
            raise HTTPException(404, "not found")
        lines = [dict(x) for x in c.execute("SELECT account, side, amount_minor FROM journal WHERE transaction_id = ?", (tx_id,))]
    return {**_out(dict(r)), "journal": lines}


@app.post("/api/transactions/{tx_id}/category")
def set_category(tx_id: int, p: Reclass):
    try:
        return _out(service.reclassify(tx_id, p.category))
    except KeyError:
        raise HTTPException(404, "not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/periods")
def periods():
    with db.conn() as c:
        return ["all"] + [r[0] for r in c.execute("SELECT DISTINCT substr(occurred_at,1,7) FROM transactions ORDER BY 1 DESC")]


@app.get("/api/reports/{period}")
def reports(period: str = "all"):
    with db.conn() as c:
        s = ledger.statements(c, period)
        s["trial_balance"] = ledger.trial_balance(c, period)
    s["review_count"] = service.review_count()
    return s


@app.post("/api/telegram/summary")
def telegram_summary(period: str | None = None):
    from . import telegram

    _throttle("summary", 20)
    ok = telegram.send(service.summary_text(period))
    return {"sent": ok}


@app.get("/api/near/fingerprint/{period}")
def near_fingerprint(period: str):
    return anchor.fingerprints(period)


@app.post("/api/near/anchor/{period}")
def near_anchor(period: str):
    _throttle("anchor", 45)
    try:
        return anchor.anchor(period)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.get("/api/near/verify/{period}")
def near_verify(period: str):
    try:
        return anchor.verify(period)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@app.get("/health")
def health():
    return {"ok": True}
