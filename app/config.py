"""Settings, read from the environment (.env is loaded in app/__init__.py)."""
import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


@dataclass(frozen=True)
class Settings:
    db_path: str
    business_name: str
    currency: str
    ingest_api_key: str
    # NEAR AI Cloud (OpenAI-compatible, private inference in TEEs)
    near_ai_key: str
    near_ai_base: str
    near_ai_model: str
    near_ai_verify: bool
    review_threshold: float
    # Telegram
    telegram_token: str
    telegram_chat_id: str
    # Gmail / IMAP (optional)
    imap_host: str
    imap_user: str
    imap_password: str
    imap_folder: str
    imap_sender_allowlist: list[str]
    # NEAR testnet anchoring
    near_network: str
    near_rpc: str
    books_contract: str
    # hosted public demo: rate limits, a cap on stored transactions, a Start over button
    public_demo: bool
    max_tx: int


def load() -> Settings:
    return Settings(
        db_path=_get("DB_PATH", "data/ai_cfo.db"),
        business_name=_get("BUSINESS_NAME", "Demo Business Ltd"),
        currency=_get("CURRENCY", "NGN"),
        ingest_api_key=_get("INGEST_API_KEY", "dev-ingest-key"),
        near_ai_key=_get("NEAR_AI_API_KEY"),
        near_ai_base=_get("NEAR_AI_BASE_URL", "https://cloud-api.near.ai/v1").rstrip("/"),
        # A "Private" model: data stays encrypted inside TEE hardware (see README).
        near_ai_model=_get("NEAR_AI_MODEL", "Qwen/Qwen3.8-27B"),
        near_ai_verify=_get("NEAR_AI_VERIFY", "true").lower() != "false",
        review_threshold=float(_get("REVIEW_THRESHOLD", "0.75")),
        telegram_token=_get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_get("TELEGRAM_CHAT_ID"),
        imap_host=_get("IMAP_HOST", "imap.gmail.com"),
        imap_user=_get("IMAP_USER"),
        imap_password=_get("IMAP_PASSWORD"),
        imap_folder=_get("IMAP_FOLDER", "INBOX"),
        imap_sender_allowlist=[s.strip().lower() for s in _get("IMAP_SENDER_ALLOWLIST").split(",") if s.strip()],
        near_network=_get("NEAR_NETWORK", "testnet"),
        near_rpc=_get("NEAR_RPC_URL", "https://test.rpc.fastnear.com"),
        books_contract=_get("BOOKS_CONTRACT_ID"),
        public_demo=_get("PUBLIC_DEMO", "false").lower() == "true",
        max_tx=int(_get("MAX_TRANSACTIONS", "60")),
    )


settings = load()
