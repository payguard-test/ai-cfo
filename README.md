# AI CFO

An AI bookkeeping agent for small businesses. It reads the bank alert emails a business already receives, classifies every transaction privately on **NEAR AI Cloud**, keeps double-entry books, sends **Telegram** updates, and produces the profit and loss, balance sheet, cash flow and capital position. Each period's books are fingerprinted on a **NEAR** contract, so anyone shown them later can check they were not changed.

Built at NEAR HACKATHON. Tracks: Real-World Money (`realworld`), Privacy & Private AI (`privacy`).

**Live demo:** https://ai-cfo-quzw.onrender.com (click Start over, then Send all samples; the first load can take up to a minute while the free server wakes). Recorded snapshot: https://payguard-test.github.io/ai-cfo/snapshot.html
**Demo video:** see the X post linked in the submission.

## The problem

Small businesses in Nigeria get a bank alert for every naira that moves, but the books are still kept by hand, late or not at all. Owners cannot say what they made this month, how much of the money in the bank is a loan or their own capital, or prove their numbers to a lender. Sending raw bank data to a general AI service is a privacy risk, and an AI that treats every credit as "sales" produces wrong statements.

## What it does

1. **Reads bank alerts.** Parses the common Nigerian alert layouts ("Txn Type: CR", "Amount: NGN 5,000.00", "Narration:", "Avail Bal:"). Sources: the bundled sample alerts, a pasted email, the API, or a real mailbox over IMAP (optional, read-only, sender allowlist).
2. **Classifies privately.** Account numbers are masked, then only structured facts are sent to a **Private** model on NEAR AI Cloud (`Qwen/Qwen3.8-27B` by default), which runs inside a TEE. The agent stores the chat id and checks NEAR AI's signed proof: the response hash is in the signed text, the ECDSA signature recovers to the TEE signing address, and that address appears in the model's attestation report.
3. **Does not guess.** A credit is not automatically sales: owner capital, loans, refunds and transfers between the owner's own accounts are separate categories. An answer that is not a valid category for the direction falls back to rules. Anything under 75% confidence, or too vague (a bare "Transfer" from a person), goes to a review queue.
4. **Keeps real books.** Every transaction posts balanced double-entry lines. Statements are built from the journal: income statement, balance sheet (always balances), cash flow (operating, investing, financing), capital summary and trial balance.
5. **Tells the owner.** A Telegram message for every transaction, a daily summary, and a bot that answers `/summary`, `/pnl`, `/balance`, `/review`.
6. **Makes the books tamper-evident on NEAR.** `books-anchor` stores, per period, a SHA-256 root of all transactions and a hash of the statements. No amounts, names or account numbers go on chain. The app signs with a function-call key that can only call `anchor`.

## Try it

### Option A: run the agent on the sample alerts (2 minutes, nothing to sign up for)

Needs Python 3.10+.

1. `python3 -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env`
4. `python scripts/demo.py`
5. You see 19 transactions with their category, confidence and status, then the income statement and balance sheet, ending with `Balances: True   Trial balance balanced: True` and `Items needing review: 1` (the vague "Transfer" from John Doe).

Without `NEAR_AI_API_KEY` it uses the keyword rules; with a key every line says `near-ai` and `proof=verified`.

### Option B: the dashboard

1. Do Option A steps 1 to 3, then `uvicorn app.main:app --reload`.
2. Open http://127.0.0.1:8000 and click **Send all samples**. You see the KPIs fill in, 19 transactions, and 1 marked **needs review**.
3. Change the John Doe transaction's category to "Sales revenue". You see Net revenue go up by ₦40,000 and **To review** drop to 0.
4. Click any transaction to see its journal lines, the AI's reason and the private-inference proof.
5. Open the statements tabs: Profit and loss, Balance sheet ("Balances: Yes"), Cash flow, Capital, Trial balance.

### Option C: with NEAR AI Cloud, Telegram and NEAR testnet

1. **NEAR AI Cloud:** create a key at https://cloud.near.ai and set `NEAR_AI_API_KEY` in `.env`. Run `python scripts/demo.py`. You see `near-ai` and `proof=verified` on each line.
2. **Telegram:** create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN`, send your bot a message, run `python -m app.telegram chat-id`, and put the printed `TELEGRAM_CHAT_ID` in `.env`. Run the demo again. You get a message per transaction. `python -m app.telegram bot` answers `/summary`, `/pnl`, `/balance`, `/review`.
3. **NEAR testnet:** set `OWNER_ACCOUNT_ID` / `OWNER_PRIVATE_KEY` (a funded `.testnet` account, about 2.5 NEAR), build the contract (`cd contracts/books-anchor && cargo near build non-reproducible-wasm --no-abi`), then `npm --prefix near install && npm --prefix near run setup`. You see the new contract account and `saved to .env`. In the dashboard click **Anchor this period on NEAR**, then **Verify against NEAR**. You see `✓ books match what was anchored on NEAR` and a testnet transaction link. Change any category and verify again: you see `✗ books changed since they were anchored`.

### Run the tests

1. `pytest -q`. You see `22 passed`: parsing of three alert layouts, masking, de-duplication, the rules on all 19 samples, the NEAR AI path against a simulated TEE (a valid proof verifies; a tampered response or an unknown signer does not), statements that balance and tie out to cash, review and correction, fingerprints, and the API.
2. `cd contracts/books-anchor && cargo test`. You see `7 passed`.

## How NEAR is used

| Piece | What it does |
|---|---|
| NEAR AI Cloud | Private inference in a TEE for every classification; signed proof checked and stored per transaction |
| `books-anchor` contract (Rust, near-sdk 5.30.1) | Per-period SHA-256 fingerprints of the books and statements, with history; `verify(period, books_root, report_hash)` view |
| Function-call key | The app can only call `anchor`; the owner (separate account) controls writers |
| Public verification | The demo site reads the contract from the browser and compares it with the published books |

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/ingest/email` | `{subject, body, sender}`, header `X-Ingest-Key` |
| GET | `/api/transactions[?status=review]` | |
| POST | `/api/transactions/{id}/category` | `{category}`; re-posts the journal |
| GET | `/api/reports/{period}` | `all` or `YYYY-MM`: income statement, balance sheet, cash flow, capital, trial balance |
| GET | `/api/near/fingerprint/{period}` | books root and statements hash |
| POST | `/api/near/anchor/{period}` | writes the fingerprints to NEAR testnet |
| GET | `/api/near/verify/{period}` | compares with the chain |
| POST | `/api/telegram/summary` | |

Interactive docs at `/docs`.

## Privacy and limits

* Only masked, structured facts reach the model. The full email, account numbers, PINs and OTPs never do.
* Private-inference proof: hashes, signature and attestation membership are checked; the raw NVIDIA and Intel hardware quotes are not re-verified here.
* Statements are cash basis, built from bank alerts: no accruals, depreciation or stock count. Not audited or tax-ready.
* The dashboard has no login; run it locally or put it behind authentication.
* Bank alert formats vary. Unreadable alerts are rejected, not guessed.

## Layout

```
app/parser.py        bank alert parsing, masking, de-duplication
app/classifier.py    NEAR AI Cloud classification with rule fallback
app/nearai.py        NEAR AI Cloud client and private-inference proof check
app/ledger.py        double-entry posting and statements
app/service.py       the agent loop
app/telegram.py      notifications and bot
app/inbox.py         optional IMAP reader
app/anchor.py        fingerprints, anchor and verify
app/static/          dashboard
contracts/books-anchor   NEAR contract
near/                testnet setup and signing (near-api-js)
samples/alerts.json  19 sample alerts from fictional banks
scripts/             demo run and public-site export
docs/                GitHub Pages snapshot
```

## Built with

Python, FastAPI, SQLite, eth-account, NEAR AI Cloud, near-sdk 5.30.1, cargo-near, near-api-js 7.4.0, Telegram Bot API. The project started from a small starter skeleton provided by the team (about 150 lines: regex parsing, keyword rules, a hash-only contract stub); that is the baseline, and everything else was written during the hackathon window with help from Claude, an AI assistant.

## License

MIT, see `LICENSE`.
