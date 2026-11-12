from fastapi.testclient import TestClient

from conftest import SAMPLES


def load_all():
    from app import service

    for s in SAMPLES:
        service.ingest_email(s["subject"], s["body"], s["sender"], source="sample")


def test_statements_balance_and_add_up(fresh_db):
    from app import db, ledger

    load_all()
    with db.conn() as c:
        s = ledger.statements(c, "all")
        tb = ledger.trial_balance(c, "all")
    inc, bs, cf = s["income_statement"], s["balance_sheet"], s["cash_flow"]
    assert inc["revenue"]["Net revenue"] == (125_000 + 64_200 + 210_000 - 15_000) * 100
    assert inc["cost_of_sales"]["Total"] == 180_000 * 100
    assert bs["balances"] and tb["balanced"]
    assert bs["liabilities"]["Loans payable"] == (1_000_000 - 83_333) * 100
    assert s["capital"]["Owner capital invested"] == 500_000 * 100
    assert bs["assets"]["Cash at bank"] == cf["net_change_in_cash"]  # cash ties out to the cash-flow statement
    assert bs["liabilities"]["Unclassified money in (needs review)"] == 40_000 * 100


def test_duplicate_alert_posts_once(fresh_db):
    import pytest

    from app import service

    s = SAMPLES[4]
    service.ingest_email(s["subject"], s["body"], s["sender"])
    with pytest.raises(service.Duplicate):
        service.ingest_email(s["subject"], s["body"], s["sender"])


def test_review_then_confirm_updates_books(fresh_db):
    from app import db, ledger, service

    load_all()
    assert service.review_count() == 1
    with db.conn() as c:
        tx_id = c.execute("SELECT id FROM transactions WHERE status='review'").fetchone()[0]
        before = ledger.statements(c, "all")["income_statement"]["revenue"]["Net revenue"]
    service.reclassify(tx_id, "sales")
    with db.conn() as c:
        s = ledger.statements(c, "all")
    assert service.review_count() == 0
    assert s["income_statement"]["revenue"]["Net revenue"] == before + 40_000 * 100
    assert "Unclassified money in (needs review)" not in s["balance_sheet"]["liabilities"] and s["balance_sheet"]["balances"]


def test_reclassify_rejects_wrong_direction(fresh_db):
    import pytest

    from app import service

    row = service.ingest_email(SAMPLES[2]["subject"], SAMPLES[2]["body"], SAMPLES[2]["sender"])
    with pytest.raises(ValueError):
        service.reclassify(row["id"], "sales")


def test_fingerprint_changes_when_books_change(fresh_db):
    from app import anchor, service

    load_all()
    a = anchor.fingerprints("2026-11")
    assert a["tx_count"] == 19 and len(a["books_root"]) == 64
    assert anchor.fingerprints("2026-11") == a  # deterministic
    service.reclassify(11, "sales")
    b = anchor.fingerprints("2026-11")
    assert b["books_root"] != a["books_root"] and b["report_hash"] != a["report_hash"]


def test_api_flow(fresh_db):
    from app.main import app

    c = TestClient(app)
    assert c.get("/health").json() == {"ok": True}
    r = c.post("/api/ingest/email", json={"subject": SAMPLES[0]["subject"], "body": SAMPLES[0]["body"], "sender": SAMPLES[0]["sender"]}, headers={"X-Ingest-Key": "dev-ingest-key"})
    assert r.status_code == 200 and r.json()["category"] == "capital"
    assert c.post("/api/ingest/email", json={"body": SAMPLES[0]["body"], "subject": SAMPLES[0]["subject"], "sender": SAMPLES[0]["sender"]}, headers={"X-Ingest-Key": "dev-ingest-key"}).status_code == 409
    assert c.post("/api/ingest/email", json={"body": "x"}, headers={"X-Ingest-Key": "wrong"}).status_code == 401
    assert c.post("/api/ingest/email", json={"body": "hello there"}, headers={"X-Ingest-Key": "dev-ingest-key"}).status_code == 422
    assert c.post("/api/demo/sample/10").json()["status"] == "review"
    rep = c.get("/api/reports/2026-11").json()
    assert rep["balance_sheet"]["balances"] and rep["trial_balance"]["balanced"] and rep["review_count"] == 1
    assert c.get("/").status_code == 200
