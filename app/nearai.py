"""NEAR AI Cloud client: OpenAI-compatible chat completions with private (TEE) inference,
plus verification that a response really came from attested hardware.

Docs: https://docs.near.ai/cloud/verification/chat
Flow: send the exact request bytes, keep the exact response text, then fetch the
signature for the chat id. The signed text contains sha256(request) and
sha256(response). We check both hashes, recover the ECDSA signer, and check that the
signer address appears in the model's (or gateway's) TEE attestation report.
Full hardware-quote verification (NVIDIA NRAS / Intel TDX) is out of scope here and
is stated as such in the stored proof.
"""
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field

import requests

from .config import settings


@dataclass
class Completion:
    content: str
    chat_id: str
    model: str
    request_body: str
    response_text: str


@dataclass
class Proof:
    status: str  # verified | unverified | unavailable
    detail: str
    signature_kind: str = ""
    signing_address: str = ""
    signed_text: str = ""
    request_hash: str = ""
    response_hash: str = ""
    checks: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.__dict__)


class NearAI:
    def __init__(self, api_key: str | None = None, base: str | None = None, model: str | None = None, session=None):
        self.key = api_key if api_key is not None else settings.near_ai_key
        self.base = (base or settings.near_ai_base).rstrip("/")
        self.model = model or settings.near_ai_model
        self.http = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "accept": "application/json"}

    def chat(self, messages: list[dict], max_tokens: int = 2000) -> Completion:
        body = json.dumps(
            {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": max_tokens, "stream": False},
            separators=(",", ":"),
        )
        r = None
        for attempt in range(4):  # flaky networks: retry connection errors, timeouts, 429 and 5xx
            try:
                r = self.http.post(f"{self.base}/chat/completions", data=body.encode(), headers=self._headers(), timeout=90)
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
            break
        text = r.content.decode("utf-8")  # hash the exact bytes the gateway signed
        if r.status_code != 200:
            raise RuntimeError(f"NEAR AI Cloud answered {r.status_code}: {text[:300]}")
        data = json.loads(text)
        content = data["choices"][0]["message"].get("content") or ""
        return Completion(content=content, chat_id=data.get("id", ""), model=data.get("model", self.model), request_body=body, response_text=text)

    # ---------- verification ----------

    def _signature(self, chat_id: str, tries: int = 4) -> dict | None:
        url = f"{self.base}/signature/{chat_id}"
        for i in range(tries):
            r = self.http.get(url, params={"model": self.model, "signing_algo": "ecdsa"}, headers=self._headers(), timeout=30)
            if r.status_code == 200:
                return r.json()
            time.sleep(1 + i)  # signature may be cached on another TEE node; retry
        return None

    def _attestation(self, with_model: bool) -> dict | None:
        params = {"signing_algo": "ecdsa", "nonce": secrets.token_hex(32)}
        if with_model:
            params["model"] = self.model
        r = self.http.get(f"{self.base}/attestation/report", params=params, headers=self._headers(), timeout=60)
        return r.json() if r.status_code == 200 else None

    def verify(self, c: Completion) -> Proof:
        req_hash = hashlib.sha256(c.request_body.encode()).hexdigest()
        resp_hash = hashlib.sha256(c.response_text.encode()).hexdigest()
        sig = self._signature(c.chat_id) if c.chat_id else None
        if not sig:
            return Proof("unavailable", "signature not available for this chat id yet", request_hash=req_hash, response_hash=resp_hash)
        text, signature, address = sig.get("text", ""), sig.get("signature", ""), sig.get("signing_address", "")
        kind = sig.get("signature_kind") or ("provider_tee" if text.count(":") >= 2 else "gateway")
        checks = {"response_hash_signed": resp_hash in text, "request_hash_signed": req_hash in text}
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct

            recovered = Account.recover_message(encode_defunct(text=text), signature=signature)
            checks["signature_valid"] = recovered.lower() == address.lower()
        except Exception as e:  # noqa: BLE001
            checks["signature_valid"] = False
            checks["signature_error"] = str(e)[:120]
        att = self._attestation(with_model=(kind == "provider_tee"))
        checks["signer_in_attestation"] = bool(att) and address.lower() in json.dumps(att).lower()
        ok = all(checks.get(k) for k in ("response_hash_signed", "signature_valid", "signer_in_attestation"))
        detail = (
            "response signed inside an attested TEE; hardware quote (NVIDIA NRAS / Intel TDX) not re-verified here"
            if ok else "one or more checks failed, see checks"
        )
        return Proof("verified" if ok else "unverified", detail, kind, address, text, req_hash, resp_hash, checks)
