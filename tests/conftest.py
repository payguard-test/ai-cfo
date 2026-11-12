import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_PATH", "/tmp/ai_cfo_test_import.db")
os.environ["NEAR_AI_API_KEY"] = ""  # tests never call the real service
os.environ["TELEGRAM_BOT_TOKEN"] = ""

SAMPLES = json.load(open(os.path.join(os.path.dirname(__file__), "..", "samples", "alerts.json"), encoding="utf8"))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    from app import config, db

    path = str(tmp_path / "t.db")
    monkeypatch.setattr(config, "settings", config.settings.__class__(**{**config.settings.__dict__, "db_path": path}))
    for mod in ("app.db", "app.service", "app.anchor", "app.main", "app.ledger"):
        m = sys.modules.get(mod)
        if m is not None and hasattr(m, "settings"):
            monkeypatch.setattr(m, "settings", config.settings)
    db.init()
    return path


class FakeResp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


class FakeTEE:
    """Stands in for NEAR AI Cloud: answers chat, signs like a TEE, serves an attestation."""

    def __init__(self, answer: dict | str, tamper: bool = False, unknown_signer: bool = False):
        from eth_account import Account

        self.acct = Account.create()
        self.other = Account.create()
        self.answer = answer
        self.tamper = tamper
        self.unknown_signer = unknown_signer
        self.sent_body = ""
        self.resp_text = ""

    def post(self, url, data=None, headers=None, timeout=None):
        import hashlib

        self.sent_body = data.decode()
        content = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        self.resp_text = json.dumps({"id": "chatcmpl-abc123", "model": "openai/gpt-oss-120b", "choices": [{"message": {"role": "assistant", "content": content}}]})
        self.req_hash = hashlib.sha256(self.sent_body.encode()).hexdigest()
        self.resp_hash = hashlib.sha256(self.resp_text.encode()).hexdigest()
        return FakeResp(200, self.resp_text)

    def get(self, url, params=None, headers=None, timeout=None):
        from eth_account.messages import encode_defunct

        if "/signature/" in url:
            resp_hash = "0" * 64 if self.tamper else self.resp_hash
            text = f"openai/gpt-oss-120b:{self.req_hash}:{resp_hash}"
            sig = self.acct.sign_message(encode_defunct(text=text)).signature.hex()
            return FakeResp(200, {"text": text, "signature": "0x" + sig.removeprefix("0x"), "signing_address": self.acct.address, "signing_algo": "ecdsa", "signature_kind": "provider_tee"})
        if "/attestation/report" in url:
            addr = self.other.address if self.unknown_signer else self.acct.address
            return FakeResp(200, {"model_attestations": [{"signing_address": addr, "nvidia_payload": "{}", "intel_quote": "00"}]})
        return FakeResp(404, {})
