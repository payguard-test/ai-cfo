import json

from app.classifier import classify, rules_classify
from app.nearai import NearAI
from app.parser import parse_email
from conftest import SAMPLES, FakeTEE

EXPECTED = ["capital", "loan_received", "rent", "cost_of_sales", "sales", "utilities", "sales", "asset_purchase", "salaries",
            "bank_charges", "uncategorized_in", "owner_drawings", "refund_paid", "transfer_out", "transfer_in", "sales",
            "marketing", "loan_repayment", "taxes"]


def txs():
    return [parse_email(s["subject"], s["body"], s["sender"]).to_dict() for s in SAMPLES]


def test_rules_get_every_sample_right():
    assert [rules_classify(t).category for t in txs()] == EXPECTED


def test_credit_is_not_automatically_sales():
    t = txs()[0]
    assert rules_classify(t).category == "capital"


def test_ai_path_uses_model_answer_and_verifies_proof():
    tee = FakeTEE({"category": "capital", "confidence": 0.97, "reason": "owner funding"})
    ai = NearAI(api_key="k", base="https://fake/v1", model="openai/gpt-oss-120b", session=tee)
    r = classify(txs()[0], ai)
    assert (r.category, r.classifier, r.ai_chat_id) == ("capital", "near-ai", "chatcmpl-abc123")
    proof = json.loads(r.ai_proof)
    assert proof["status"] == "verified", proof
    assert all(proof["checks"][k] for k in ("response_hash_signed", "request_hash_signed", "signature_valid", "signer_in_attestation"))


def test_prompt_never_contains_account_number():
    tee = FakeTEE({"category": "capital", "confidence": 0.9, "reason": "x"})
    ai = NearAI(api_key="k", base="https://fake/v1", session=tee)
    t = txs()[0]
    t["narration"] += " acct 0123456789"
    classify(t, ai, verify=False)
    assert "0123456789" not in tee.sent_body and "******6789" in tee.sent_body


def test_tampered_response_is_not_verified():
    tee = FakeTEE({"category": "capital", "confidence": 0.9, "reason": "x"}, tamper=True)
    r = classify(txs()[0], NearAI(api_key="k", base="https://fake/v1", session=tee))
    assert json.loads(r.ai_proof)["status"] == "unverified"


def test_signer_not_in_attestation_is_not_verified():
    tee = FakeTEE({"category": "capital", "confidence": 0.9, "reason": "x"}, unknown_signer=True)
    r = classify(txs()[0], NearAI(api_key="k", base="https://fake/v1", session=tee))
    assert json.loads(r.ai_proof)["status"] == "unverified"


def test_invalid_ai_category_falls_back_to_rules():
    tee = FakeTEE({"category": "sales", "confidence": 0.9, "reason": "wrong direction"})  # sales is not valid for a debit
    r = classify(txs()[2], NearAI(api_key="k", base="https://fake/v1", session=tee))
    assert (r.category, r.classifier) == ("rent", "rules")


def test_garbage_ai_answer_falls_back():
    tee = FakeTEE("I think this might be rent?")
    r = classify(txs()[2], NearAI(api_key="k", base="https://fake/v1", session=tee))
    assert r.classifier == "rules" and r.category == "rent"


def test_no_key_uses_rules():
    assert classify(txs()[2], NearAI(api_key="")).classifier == "rules"
