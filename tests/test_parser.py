import pytest

from app.parser import ParseError, fingerprint, mask_digits, parse_date, parse_email, to_minor
from conftest import SAMPLES


def test_all_samples_parse():
    for s in SAMPLES:
        a = parse_email(s["subject"], s["body"], s["sender"])
        assert a.amount_minor > 0 and a.direction in ("credit", "debit")
        assert len(a.occurred_at) == 10


def test_three_layouts():
    a = parse_email(SAMPLES[0]["subject"], SAMPLES[0]["body"], SAMPLES[0]["sender"])
    assert (a.amount_minor, a.direction, a.occurred_at, a.bank, a.reference) == (50_000_000, "credit", "2026-11-01", "Lagoon Bank", "LGB2611010001")
    assert a.account_masked == "******6789" and a.balance_after_minor == 50_000_000
    b = parse_email(SAMPLES[1]["subject"], SAMPLES[1]["body"], SAMPLES[1]["sender"])
    assert (b.amount_minor, b.direction, b.occurred_at, b.bank) == (100_000_000, "credit", "2026-11-02", "Harbor MFB")
    c = parse_email(SAMPLES[3]["subject"], SAMPLES[3]["body"], SAMPLES[3]["sender"])
    assert (c.amount_minor, c.direction, c.bank, c.counterparty) == (18_000_000, "debit", "Cowrie Pay", "MILE 12 FOODSTUFF WHOLESALE")


def test_sender_beats_body_for_bank():
    s = SAMPLES[13]  # Harbor MFB alert that mentions Lagoon Bank in the narration
    assert parse_email(s["subject"], s["body"], s["sender"]).bank == "Harbor MFB"


def test_amount_and_dates():
    assert to_minor("1,234.5") == 123450 and to_minor("53.75") == 5375 and to_minor("100") == 10000
    assert parse_date("08-Nov-2026") == "2026-11-08"
    assert parse_date("08/11/2026") == "2026-11-08"  # DD/MM/YYYY
    assert parse_date("Nov 8, 2026") == "2026-11-08"


def test_masks_account_numbers_only():
    assert mask_digits("acct 0123456789 paid N250,000.00 ref 4471") == "acct ******6789 paid N250,000.00 ref 4471"


def test_unreadable_alert_raises():
    with pytest.raises(ParseError):
        parse_email("Hello", "Your statement is ready")


def test_fingerprint_dedupes_same_alert():
    a = parse_email(SAMPLES[4]["subject"], SAMPLES[4]["body"], SAMPLES[4]["sender"])
    b = parse_email(SAMPLES[4]["subject"], SAMPLES[4]["body"] + "\n", SAMPLES[4]["sender"])
    assert fingerprint(a) == fingerprint(b)
