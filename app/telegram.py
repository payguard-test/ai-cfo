"""Telegram notifications and a small command bot.

  python -m app.telegram chat-id   print the chat id after you message your bot once
  python -m app.telegram bot       answer /summary /pnl /balance /review in the chat
  python -m app.telegram daily     send today's summary (schedule with cron if wanted)
"""
import html
import sys
import time

import requests

from .config import settings

API = "https://api.telegram.org/bot{token}/{method}"


def enabled() -> bool:
    return bool(settings.telegram_token and settings.telegram_chat_id)


MAX_SUBSCRIBERS = 200


def _post(text: str, chat_id: str) -> bool:
    try:
        r = requests.post(
            API.format(token=settings.telegram_token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        return r.ok
    except requests.RequestException:
        return False


def recipients() -> list[str]:
    """The owner's chat, plus (on the public demo) everyone who pressed Start in the bot."""
    out = [str(settings.telegram_chat_id)] if settings.telegram_chat_id else []
    if settings.public_demo:
        from . import db

        with db.conn() as c:
            out += [r["chat_id"] for r in c.execute("SELECT chat_id FROM subscribers ORDER BY added_at LIMIT ?", (MAX_SUBSCRIBERS,))]
    return list(dict.fromkeys(out))


def send(text: str, chat_id: str | None = None) -> bool:
    if not settings.telegram_token:
        return False
    if chat_id:
        return _post(text, str(chat_id))
    targets = recipients()
    if not targets:
        return False
    first = _post(text, targets[0])
    if len(targets) > 1:  # don't make the dashboard wait for every subscriber
        import threading

        threading.Thread(target=lambda: [_post(text, t) for t in targets[1:]], daemon=True).start()
    return first


_ME: dict = {}


def bot_username() -> str:
    if not settings.telegram_token:
        return ""
    if "u" not in _ME:
        try:
            r = requests.get(API.format(token=settings.telegram_token, method="getMe"), timeout=10).json()
            _ME["u"] = r.get("result", {}).get("username", "") if r.get("ok") else ""
        except requests.RequestException:
            return ""
    return _ME["u"]


def webhook_secret() -> str:
    import hashlib

    return hashlib.sha256(("webhook:" + settings.telegram_token).encode()).hexdigest()[:40]


def set_webhook(base_url: str) -> bool:
    try:
        r = requests.post(API.format(token=settings.telegram_token, method="setWebhook"),
                          json={"url": base_url.rstrip("/") + "/api/telegram/webhook", "secret_token": webhook_secret(),
                                "allowed_updates": ["message"], "drop_pending_updates": True}, timeout=15)
        return r.ok
    except requests.RequestException:
        return False


WELCOME = ("<b>AI CFO demo</b> 👋\nYou'll now get a message for every bank alert the agent records on the live demo.\n"
           "Open the demo, click <b>Start over</b>, then <b>Send all samples</b>, and watch them arrive here.\n\n"
           "Ask me any time: /summary /pnl /balance /review\nStop the messages: /stop")


def handle_update(update: dict) -> None:
    from . import db, service

    msg = update.get("message") or {}
    chat = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").split("@")[0].strip().lower()
    if not chat or not text:
        return
    owner = chat == str(settings.telegram_chat_id)
    if not settings.public_demo and not owner:
        _post("This bot is private.", chat)
        return
    if text.startswith("/start"):
        if settings.public_demo and not owner:
            with db.conn() as c:
                n = c.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
                if n < MAX_SUBSCRIBERS:
                    c.execute("INSERT OR IGNORE INTO subscribers(chat_id, name) VALUES (?, ?)", (chat, (msg.get("chat", {}).get("first_name") or "")[:60]))
        _post(WELCOME, chat)
        return
    if text == "/stop":
        with db.conn() as c:
            c.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat,))
        _post("Stopped. Send /start to get the messages again.", chat)
        return
    _post(service.command_reply(text), chat)


def money(minor: int, currency: str = "NGN") -> str:
    sym = "₦" if currency == "NGN" else ("$" if currency == "USD" else currency + " ")
    sign = "-" if minor < 0 else ""
    return f"{sign}{sym}{abs(minor) / 100:,.2f}"


def tx_message(tx: dict, label: str) -> str:
    arrow = "⬇️ Money in" if tx["direction"] == "credit" else "⬆️ Money out"
    e = html.escape
    lines = [
        f"<b>{arrow}: {money(tx['amount_minor'], tx['currency'])}</b>",
        f"{e(tx['counterparty'])} · {e(tx['bank'])}",
        f"<i>{e(tx['narration'][:120])}</i>",
        f"Recorded as: <b>{e(label)}</b> ({tx['confidence']:.0%} sure, {e(tx['classifier'])})",
    ]
    if tx["status"] == "review":
        lines.append("⚠️ Needs your review in the dashboard.")
    if tx.get("ai_proof_status") == "verified":
        lines.append("🔒 Classified privately on NEAR AI Cloud (TEE proof verified)")
    return "\n".join(lines)


def summary_message(s: dict, business: str, review_count: int, period: str) -> str:
    inc, bs = s["income_statement"], s["balance_sheet"]
    return "\n".join([
        f"<b>📊 {html.escape(business)}: {html.escape(period)}</b>",
        f"Revenue: {money(inc['revenue']['Net revenue'])}",
        f"Cost of sales: {money(inc['cost_of_sales']['Total'])}",
        f"Expenses: {money(inc['total_operating_expenses'])}",
        f"<b>Net profit: {money(inc['net_profit'])}</b>",
        f"Cash at bank: {money(bs['assets']['Cash at bank'])}",
        f"Owner capital: {money(s['capital']['Owner capital invested'])} · Loans: {money(s['capital']['Loans outstanding'])}",
        f"Items to review: {review_count}",
    ])


def _updates(offset: int | None) -> list[dict]:
    r = requests.get(API.format(token=settings.telegram_token, method="getUpdates"), params={"timeout": 30, "offset": offset}, timeout=40)
    return r.json().get("result", []) if r.ok else []


def main(argv: list[str]) -> None:
    if not settings.telegram_token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env first.")
    cmd = argv[1] if len(argv) > 1 else "chat-id"
    if cmd == "chat-id":
        ups = _updates(None)
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or u["message"]["chat"].get("title") for u in ups if "message" in u}
        if not chats:
            sys.exit("No messages yet. Open your bot in Telegram, press Start, send 'hi', then run this again.")
        for cid, name in chats.items():
            print(f"TELEGRAM_CHAT_ID={cid}   ({name})")
        return
    from . import service  # local import: service imports this module

    if cmd == "daily":
        ok = send(service.summary_text())
        print("sent" if ok else "not sent (check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
        return
    if cmd == "bot":
        print("Bot running. Commands: /summary /pnl /balance /review. Ctrl+C to stop.")
        offset = None
        while True:
            for u in _updates(offset):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str(msg.get("chat", {}).get("id", ""))
                if chat != str(settings.telegram_chat_id):
                    continue  # only the owner's chat may read the books
                text = (msg.get("text") or "").split("@")[0].strip().lower()
                send(service.command_reply(text), chat)
            time.sleep(1)
    sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main(sys.argv)
