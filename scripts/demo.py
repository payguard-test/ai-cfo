"""Run the whole agent on the sample alerts from the command line.

  python scripts/demo.py           fresh database, ingest all 19 sample alerts, print the statements
  python scripts/demo.py --keep    keep the existing database
Uses NEAR AI Cloud if NEAR_AI_API_KEY is set, otherwise the keyword rules.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, ledger, service  # noqa: E402
from app.config import settings  # noqa: E402
from app.telegram import money  # noqa: E402

if "--keep" not in sys.argv and os.path.exists(settings.db_path):
    os.remove(settings.db_path)
db.init()
samples = json.load(open(os.path.join(os.path.dirname(__file__), "..", "samples", "alerts.json"), encoding="utf8"))
mode = f"NEAR AI Cloud, model {settings.near_ai_model}" if settings.near_ai_key else "keyword rules (set NEAR_AI_API_KEY to use NEAR AI Cloud)"
print(f"Classifier: {mode}\n")
for s in samples:
    try:
        r = service.ingest_email(s["subject"], s["body"], s["sender"], source="sample")
    except service.Duplicate:
        continue
    flag = "REVIEW" if r["status"] == "review" else "posted"
    proof = r.get("ai_proof_status") or "-"
    print(f"{r['occurred_at']}  {r['direction']:<6} {money(r['amount_minor']):>16}  {r['category']:<18} {r['confidence']:.0%}  {flag:<6}  proof={proof:<10} {r['narration'][:38]}")

with db.conn() as c:
    s = ledger.statements(c, "all")
    tb = ledger.trial_balance(c, "all")
inc, bs = s["income_statement"], s["balance_sheet"]
print("\nINCOME STATEMENT")
print(f"  Net revenue                {money(inc['revenue']['Net revenue']):>16}")
print(f"  Cost of sales              {money(inc['cost_of_sales']['Total']):>16}")
print(f"  Gross profit               {money(inc['gross_profit']):>16}")
for k, v in inc["operating_expenses"].items():
    print(f"    {k:<26}{money(v):>16}")
print(f"  Net profit                 {money(inc['net_profit']):>16}")
print("\nBALANCE SHEET")
for k, v in bs["assets"].items():
    print(f"  {k:<33}{money(v):>16}")
for k, v in bs["liabilities"].items():
    print(f"  {k:<33}{money(v):>16}")
for k, v in bs["equity"].items():
    print(f"  {k:<33}{money(v):>16}")
print(f"  Balances: {bs['balances']}   Trial balance balanced: {tb['balanced']}")
print(f"\nItems needing review: {service.review_count()}")
