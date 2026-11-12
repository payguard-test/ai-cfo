"""Turn a bank alert email into structured transaction facts.

Handles the common layouts of Nigerian bank and fintech alerts ("Credit Alert",
"Txn Type: DR", "Amount: NGN 5,000.00", "Narration:", "Avail Bal:" ...). When a
field cannot be found the parser says so instead of guessing; the caller can then
send the alert to the AI extractor or to the review queue.
"""
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

CURRENCY = r"(?:NGN|N|₦|USD|\$)"
NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"


class ParseError(ValueError):
    pass


@dataclass
class Alert:
    amount_minor: int
    currency: str
    direction: str  # credit | debit
    occurred_at: str  # YYYY-MM-DD
    reference: str
    counterparty: str
    narration: str
    balance_after_minor: int | None
    bank: str
    account_masked: str

    def to_dict(self) -> dict:
        return asdict(self)


def to_minor(s: str) -> int:
    s = s.replace(",", "")
    whole, _, frac = s.partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def mask_digits(text: str) -> str:
    """Mask account-number-like digit runs (6+ digits), keeping the last 4."""
    return re.sub(r"\d{6,}", lambda m: "*" * (len(m.group()) - 4) + m.group()[-4:], text)


def _field(text: str, *labels: str) -> str | None:
    for label in labels:
        m = re.search(rf"(?im)^\s*{label}\s*[:\-]\s*(.+?)\s*$", text)
        if m:
            return m.group(1).strip()
    return None


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(text: str) -> str | None:
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"\b(\d{1,2})[-/ ]([A-Za-z]{3})[A-Za-z]*[-/ ,]+(20\d{2})\b", text)
    if m and m.group(2).lower() in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    m = re.search(r"\b([A-Za-z]{3})[A-Za-z]*\.? (\d{1,2}),? (20\d{2})\b", text)
    if m and m.group(1).lower() in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)  # Nigerian alerts use DD/MM/YYYY
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    return None


KNOWN_BANKS = {
    "lagoon bank": "Lagoon Bank",
    "harbor mfb": "Harbor MFB",
    "cowrie pay": "Cowrie Pay",
    "gtbank": "GTBank",
    "guaranty trust": "GTBank",
    "access bank": "Access Bank",
    "zenith": "Zenith Bank",
    "uba": "UBA",
    "first bank": "First Bank",
    "opay": "OPay",
    "moniepoint": "Moniepoint",
    "kuda": "Kuda",
    "wema": "Wema Bank",
}


def detect_bank(text: str, sender: str = "") -> str:
    # The sender address is the most reliable signal; the body may mention other banks.
    s = sender.lower()
    for key, name in KNOWN_BANKS.items():
        if key.replace(" ", "") in s:
            return name
    low = text.lower()
    for key, name in KNOWN_BANKS.items():
        if key in low:
            return name
    m = re.search(r"@([a-z0-9-]+)\.", sender.lower())
    return m.group(1).title() if m else "Unknown"


def detect_direction(text: str) -> str | None:
    low = text.lower()
    typ = _field(text, r"(?:txn|transaction)\s*type", r"type")
    if typ:
        t = typ.lower()
        if t.startswith(("cr", "credit")):
            return "credit"
        if t.startswith(("dr", "debit")):
            return "debit"
    credit = re.search(r"\bcredit(?:ed)?\b|\bcr alert\b|\breceived\b|\bdeposit\b|\binflow\b", low)
    debit = re.search(r"\bdebit(?:ed)?\b|\bdr alert\b|\bwithdrawal\b|\bpurchase\b|\boutflow\b|\bsent\b", low)
    if credit and not debit:
        return "credit"
    if debit and not credit:
        return "debit"
    if credit and debit:  # both words present: the earlier, headline one wins
        return "credit" if credit.start() < debit.start() else "debit"
    return None


def detect_amount(text: str) -> tuple[int, str] | None:
    labelled = _field(text, r"(?:txn\s*)?amount", r"amt")
    for source in ([labelled] if labelled else []) + [text]:
        m = re.search(rf"({CURRENCY})\s?{NUM}", source)
        if m:
            cur = "USD" if m.group(1) in ("USD", "$") else "NGN"
            return to_minor(m.group(2)), cur
    return None


def detect_balance(text: str) -> int | None:
    m = re.search(rf"(?i)(?:avail(?:able)?\.?\s*bal(?:ance)?|bal(?:ance)?)\s*[:\-]?\s*{CURRENCY}?\s?{NUM}", text)
    return to_minor(m.group(1)) if m else None


def detect_counterparty(text: str, narration: str) -> str:
    for label in (r"from", r"to", r"sender", r"beneficiary", r"recipient", r"counterparty"):
        v = _field(text, label)
        if v:
            return v[:60]
    m = re.search(r"(?i)\b(?:from|to|by|frm)\s+([A-Z][A-Z0-9 &.'-]{2,40}?)(?:\s+(?:ref|for|via|on)\b|[/,.;]|$)", narration)
    if m:
        return m.group(1).strip()[:60]
    return "Unspecified"


def parse_email(subject: str, body: str, sender: str = "") -> Alert:
    text = f"{subject}\n{body}"
    amt = detect_amount(text)
    if not amt:
        raise ParseError("amount not found")
    direction = detect_direction(text)
    if not direction:
        raise ParseError("credit/debit direction not found")
    narration = _field(text, "narration", "description", "desc", "remarks?", "details") or subject.strip()
    ref = _field(text, r"ref(?:erence)?(?:\s*no)?", r"session\s*id", r"txn\s*id", r"transaction\s*id")
    acct = _field(text, r"acc(?:oun)?t(?:\s*(?:no|number))?", r"acct")
    occurred = parse_date(_field(text, r"date", r"value\s*date", r"time") or "") or parse_date(text)
    digest = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()[:16].upper()
    return Alert(
        amount_minor=amt[0],
        currency=amt[1],
        direction=direction,
        occurred_at=occurred or datetime.now(timezone.utc).date().isoformat(),
        reference=(ref.split()[0] if ref else f"H-{digest}")[:64],
        counterparty=detect_counterparty(text, narration),
        narration=mask_digits(narration)[:300],
        balance_after_minor=detect_balance(text),
        bank=detect_bank(text, sender),
        account_masked=mask_digits(acct)[:32] if acct else "",
    )


def fingerprint(a: Alert) -> str:
    """Duplicate protection: the same alert seen twice (forwarded, re-polled) posts once."""
    key = f"{a.bank}|{a.reference}|{a.amount_minor}|{a.direction}|{a.occurred_at}"
    return hashlib.sha256(key.encode()).hexdigest()
