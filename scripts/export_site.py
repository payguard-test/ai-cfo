"""Export a read-only snapshot of the dashboard for GitHub Pages (docs/).

  python scripts/export_site.py
Run it after the demo data is in the database (and after anchoring, so the public
"Verify against NEAR" button has something to check). Nothing secret is exported:
masked sample data, statements, fingerprints and the public contract id.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
c = TestClient(app)
api = {"/api/config": c.get("/api/config").json(), "/api/periods": c.get("/api/periods").json()}
api["/api/transactions"] = c.get("/api/transactions").json()
api["/api/transactions?status=review"] = [t for t in api["/api/transactions"] if t["status"] == "review"]
for p in api["/api/periods"]:
    api[f"/api/reports/{p}"] = c.get(f"/api/reports/{p}").json()
    api[f"/api/near/fingerprint/{p}"] = c.get(f"/api/near/fingerprint/{p}").json()
for t in api["/api/transactions"]:
    t["journal"] = c.get(f"/api/transactions/{t['id']}").json()["journal"]

os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
snap = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "api": api}
with open(os.path.join(ROOT, "docs", "data.js"), "w", encoding="utf8") as f:
    f.write("window.AICFO_SNAPSHOT = " + json.dumps(snap, ensure_ascii=False) + ";\n")
html = open(os.path.join(ROOT, "app", "static", "index.html"), encoding="utf8").read()
html = re.sub(r"<script>\n", '<script src="data.js"></script>\n<script>\n', html, count=1)
with open(os.path.join(ROOT, "docs", "index.html"), "w", encoding="utf8") as f:
    f.write(html)
open(os.path.join(ROOT, "docs", ".nojekyll"), "w").close()
print(f"Wrote docs/index.html and docs/data.js ({len(api['/api/transactions'])} transactions, periods {api['/api/periods']})")
