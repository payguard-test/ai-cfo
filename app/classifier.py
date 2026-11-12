"""Classify a transaction: private AI on NEAR AI Cloud first, keyword rules as fallback.

Only masked, structured facts go to the model (no account numbers, no full email).
The model must answer with a category that is valid for the transaction's direction;
anything else falls back to the rules. Low confidence goes to the review queue.
"""
import json
import re
from dataclasses import dataclass

from .categories import CATEGORIES, UNCATEGORIZED, allowed
from .nearai import NearAI
from .parser import mask_digits


@dataclass
class Result:
    category: str
    confidence: float
    reason: str
    classifier: str  # near-ai | rules | manual
    ai_model: str = ""
    ai_chat_id: str = ""
    ai_proof: str = ""


RULES: list[tuple[str, list[str]]] = [
    ("capital", ["capital injection", "owner capital", "funding from owner", "capital contribution"]),
    ("loan_received", ["loan disbursement", "loan credit", "facility disbursement"]),
    ("loan_repayment", ["loan repayment", "instalment", "installment", "facility repayment"]),
    ("transfer_in", ["from own", "own account", "internal transfer", "sweep from"]),
    ("transfer_out", ["to own", "own account", "internal transfer", "sweep to"]),
    ("refund_paid", ["refund to customer", "refund to", "chargeback"]),
    ("refund_received", ["refund from", "reversal"]),
    ("owner_drawings", ["personal withdrawal", "owner draw", "drawings", "personal use"]),
    ("bank_charges", ["sms alert", "charges", "maintenance fee", "stamp duty", "commission", "vat on"]),
    ("taxes", ["vat remittance", "firs", "tax", "levy", "lirs"]),
    ("rent", ["rent", "lease"]),
    ("salaries", ["salary", "salaries", "wages", "payroll", "staff pay"]),
    ("utilities", ["diesel", "fuel", "generator", "electricity", "nepa", "prepaid meter", "internet", "water bill"]),
    ("marketing", ["ads", "advert", "promo", "marketing", "flyer", "influencer"]),
    ("transport", ["delivery", "logistics", "transport", "dispatch", "uber", "bolt"]),
    ("cost_of_sales", ["wholesale", "foodstuff", "ingredients", "raw material", "stock", "inventory", "rice", "oil", "supplier"]),
    ("asset_purchase", ["laptop", "equipment", "machine", "freezer", "generator purchase", "furniture", "vehicle"]),
    ("sales", ["payment for", "pos settlement", "order", "invoice", "sales", "catering", "customer payment"]),
]


def rules_classify(tx: dict) -> Result:
    text = f"{tx.get('narration', '')} {tx.get('counterparty', '')}".lower()
    valid = {c.key for c in allowed(tx["direction"])}
    for key, words in RULES:
        if key not in valid:
            continue
        hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", text)), None)
        if hit:
            return Result(key, 0.8, f"keyword rule: {hit}", "rules")
    return Result(UNCATEGORIZED[tx["direction"]], 0.3, "no rule matched", "rules")


SYSTEM = """You are the bookkeeping agent for a small Nigerian business. Classify ONE bank transaction.
Rules:
- A credit is NOT automatically sales. Owner money is "capital", lender money is "loan_received", money from the owner's other account is "transfer_in".
- Stock, ingredients and goods bought for resale are "cost_of_sales". Equipment that lasts more than a year is "asset_purchase".
- Money the owner takes for personal use is "owner_drawings", not an expense.
- If the description is too vague to know (for example just "Transfer" from a person), use the uncategorized category with low confidence. Do not guess.
Answer with ONLY a JSON object: {"category": "<key>", "confidence": <0..1>, "reason": "<short reason>"}"""


def _prompt(tx: dict) -> str:
    cats = "\n".join(f"- {c.key}: {c.label}" for c in allowed(tx["direction"]))
    facts = {
        "direction": "money in (credit)" if tx["direction"] == "credit" else "money out (debit)",
        "amount": f"{tx['currency']} {tx['amount_minor'] / 100:,.2f}",
        "counterparty": mask_digits(tx.get("counterparty", "")),
        "description": mask_digits(tx.get("narration", "")),
        "bank": tx.get("bank", ""),
        "date": tx.get("occurred_at", ""),
    }
    return f"Allowed categories for this direction:\n{cats}\n\nTransaction:\n{json.dumps(facts, ensure_ascii=False)}"


def _parse(content: str) -> dict | None:
    # reasoning models may put their thinking before the answer; use the last JSON object
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    dec = json.JSONDecoder()
    found = None
    for i, ch in enumerate(content):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(content[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "category" in obj:
                found = obj
    return found


def classify(tx: dict, ai: NearAI | None = None, verify: bool = True) -> Result:
    ai = ai or NearAI()
    if not ai.enabled:
        return rules_classify(tx)
    try:
        c = ai.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(tx)}])
    except Exception as e:  # noqa: BLE001 - network or quota problems must not lose the transaction
        r = rules_classify(tx)
        r.reason += f" (NEAR AI unavailable: {str(e)[:80]})"
        return r
    out = _parse(c.content)
    valid = {x.key for x in allowed(tx["direction"])}
    if not out or out.get("category") not in valid:
        r = rules_classify(tx)
        r.reason += " (AI answer was not a valid category)"
        r.ai_model, r.ai_chat_id = c.model, c.chat_id
        return r
    try:
        conf = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    proof = ""
    if verify:
        try:
            proof = ai.verify(c).to_json()
        except Exception as e:  # noqa: BLE001
            proof = json.dumps({"status": "unavailable", "detail": f"verification error: {str(e)[:120]}"})
    return Result(out["category"], conf, str(out.get("reason", ""))[:200], "near-ai", c.model, c.chat_id, proof)


def label(key: str) -> str:
    return CATEGORIES[key].label if key in CATEGORIES else key
