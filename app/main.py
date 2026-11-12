"""AI CFO API and dashboard.  Run: uvicorn app.main:app --reload  then open http://127.0.0.1:8000"""
import json
import os

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import anchor, db, ledger, service
from .categories import CATEGORIES
from .config import settings
from .parser import ParseError

db.init()
app = FastAPI(title="AI CFO", version="1.0.0", description="Bank alerts in, classified double-entry books, statements and Telegram updates out.")
STATIC = os.path.join(os.path.dirname(__file__), "static")


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
    try:
        return _out(service.ingest_email(s["subject"], s["body"], s["sender"], source="sample"))
    except service.Duplicate as e:
        raise HTTPException(409, str(e))


@app.post("/api/demo/paste")
def paste(p: EmailIn):
    """Dashboard 'paste an alert' box. Local demo only: put the dashboard behind auth in production."""
    try:
        return _out(service.ingest_email(p.subject, p.body, p.sender, source="pasted"))
    except ParseError as e:
        raise HTTPException(422, f"could not read this alert: {e}")
    except service.Duplicate as e:
        raise HTTPException(409, str(e))


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

    ok = telegram.send(service.summary_text(period))
    return {"sent": ok}


@app.get("/api/near/fingerprint/{period}")
def near_fingerprint(period: str):
    return anchor.fingerprints(period)


@app.post("/api/near/anchor/{period}")
def near_anchor(period: str):
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
