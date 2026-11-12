"""The agent loop: alert -> facts -> classification -> double-entry -> notify."""
import json
from datetime import date

from . import db, ledger, telegram
from .categories import CATEGORIES, UNCATEGORIZED
from .classifier import Result, classify, label
from .config import settings
from .nearai import NearAI
from .parser import Alert, ParseError, fingerprint, parse_email


class Duplicate(Exception):
    pass


def _proof_status(proof: str) -> str:
    try:
        return json.loads(proof).get("status", "") if proof else ""
    except json.JSONDecodeError:
        return ""


def record(alert: Alert, source: str, ai: NearAI | None = None, forced: str | None = None, notify: bool = True) -> dict:
    tx = alert.to_dict()
    fp = fingerprint(alert)
    with db.conn() as c:
        if c.execute("SELECT 1 FROM transactions WHERE fingerprint = ?", (fp,)).fetchone():
            raise Duplicate(f"already recorded: {alert.reference}")
    if forced:
        if forced not in CATEGORIES or CATEGORIES[forced].direction != alert.direction:
            raise ValueError(f"category {forced} is not valid for a {alert.direction}")
        res = Result(forced, 1.0, "set by a person", "manual")
    else:
        res = classify(tx, ai)
    status = "review" if (res.confidence < settings.review_threshold or res.category == UNCATEGORIZED[alert.direction]) else "posted"
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO transactions(fingerprint, reference, occurred_at, amount_minor, currency, direction, counterparty,
               narration, balance_after_minor, bank, account_masked, source, category, confidence, classifier, reason, status,
               ai_model, ai_chat_id, ai_proof) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fp, alert.reference, alert.occurred_at, alert.amount_minor, alert.currency, alert.direction, alert.counterparty,
             alert.narration, alert.balance_after_minor, alert.bank, alert.account_masked, source, res.category, res.confidence,
             res.classifier, res.reason, status, res.ai_model, res.ai_chat_id, res.ai_proof),
        )
        tx_id = cur.lastrowid
        ledger.post(c, tx_id, alert.direction, res.category, alert.amount_minor)
        row = dict(c.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone())
    row["ai_proof_status"] = _proof_status(row["ai_proof"])
    if notify:
        telegram.send(telegram.tx_message(row, label(row["category"])))
    return row


def ingest_email(subject: str, body: str, sender: str = "", source: str = "email", ai: NearAI | None = None) -> dict:
    alert = parse_email(subject, body, sender)
    return record(alert, source, ai)


def reclassify(tx_id: int, category: str) -> dict:
    with db.conn() as c:
        row = c.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        if not row:
            raise KeyError(tx_id)
        if category not in CATEGORIES or CATEGORIES[category].direction != row["direction"]:
            raise ValueError(f"category {category} is not valid for a {row['direction']}")
        c.execute(
            "UPDATE transactions SET category=?, confidence=1.0, classifier='manual', reason=?, status='posted' WHERE id=?",
            (category, f"confirmed by a person (was {row['category']}, {row['classifier']})", tx_id),
        )
        ledger.post(c, tx_id, row["direction"], category, row["amount_minor"])
        return dict(c.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone())


def review_count() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) FROM transactions WHERE status='review'").fetchone()[0]


def current_period() -> str:
    with db.conn() as c:
        r = c.execute("SELECT MAX(substr(occurred_at,1,7)) FROM transactions").fetchone()[0]
    return r or date.today().strftime("%Y-%m")


def summary_text(period: str | None = None) -> str:
    p = period or current_period()
    with db.conn() as c:
        s = ledger.statements(c, p)
    return telegram.summary_message(s, settings.business_name, review_count(), p)


def command_reply(cmd: str) -> str:
    p = current_period()
    with db.conn() as c:
        s = ledger.statements(c, p)
    m = telegram.money
    if cmd in ("/start", "/help"):
        return "Commands: /summary /pnl /balance /review"
    if cmd == "/pnl":
        inc = s["income_statement"]
        lines = [f"<b>Profit and loss, {p}</b>", f"Net revenue: {m(inc['revenue']['Net revenue'])}", f"Cost of sales: {m(inc['cost_of_sales']['Total'])}", f"Gross profit: {m(inc['gross_profit'])}"]
        lines += [f"  {k}: {m(v)}" for k, v in inc["operating_expenses"].items()]
        lines.append(f"<b>Net profit: {m(inc['net_profit'])}</b>")
        return "\n".join(lines)
    if cmd == "/balance":
        bs = s["balance_sheet"]
        lines = [f"<b>Balance sheet, end of {p}</b>"] + [f"{k}: {m(v)}" for k, v in bs["assets"].items()]
        lines += [f"Loans payable: {m(bs['liabilities']['Loans payable'])}"] + [f"{k}: {m(v)}" for k, v in bs["equity"].items()]
        lines.append("Balances ✅" if bs["balances"] else "Does not balance ⚠️")
        return "\n".join(lines)
    if cmd == "/review":
        with db.conn() as c:
            rows = c.execute("SELECT * FROM transactions WHERE status='review' ORDER BY id DESC LIMIT 10").fetchall()
        if not rows:
            return "Nothing to review ✅"
        return "<b>Needs review</b>\n" + "\n".join(f"#{r['id']} {m(r['amount_minor'])} {r['direction']} · {r['counterparty']} · {r['narration'][:40]}" for r in rows)
    return summary_text(p)


def statements(period: str = "all") -> dict:
    with db.conn() as c:
        return ledger.statements(c, period)


__all__ = ["record", "ingest_email", "reclassify", "statements", "Duplicate", "ParseError"]
