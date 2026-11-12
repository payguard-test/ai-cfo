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


def send(text: str, chat_id: str | None = None) -> bool:
    if not settings.telegram_token or not (chat_id or settings.telegram_chat_id):
        return False
    try:
        r = requests.post(
            API.format(token=settings.telegram_token, method="sendMessage"),
            json={"chat_id": chat_id or settings.telegram_chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        return r.ok
    except requests.RequestException:
        return False


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
