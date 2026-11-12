"""Optional: read bank alerts from a real mailbox over IMAP (free, read-only use).

Gmail: turn on 2-step verification, create an App Password (Google Account > Security >
App passwords), then set IMAP_USER / IMAP_PASSWORD in .env. Forward your bank alerts
to that mailbox, and list the bank sender addresses in IMAP_SENDER_ALLOWLIST so nothing
else is read. Messages are only read, never deleted; each Message-ID is processed once.

  python -m app.inbox            check once
  python -m app.inbox --watch    check every 60 seconds
"""
import email
import imaplib
import sys
import time
from email.header import decode_header, make_header

from . import db, service
from .config import settings
from .parser import ParseError


def _text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                import re

                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", "\n", html)
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace")


def check_once() -> list[dict]:
    if not (settings.imap_user and settings.imap_password):
        sys.exit("Set IMAP_USER and IMAP_PASSWORD in .env (see the comment at the top of app/inbox.py).")
    db.init()
    results = []
    with imaplib.IMAP4_SSL(settings.imap_host) as m:
        m.login(settings.imap_user, settings.imap_password)
        m.select(settings.imap_folder, readonly=True)
        _, data = m.search(None, "SINCE", time.strftime("%d-%b-%Y", time.gmtime(time.time() - 7 * 86400)))
        for num in data[0].split():
            _, parts = m.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(parts[0][1])
            mid = msg.get("Message-ID", f"num-{num.decode()}")
            sender = str(make_header(decode_header(msg.get("From", "")))).lower()
            if settings.imap_sender_allowlist and not any(a in sender for a in settings.imap_sender_allowlist):
                continue
            with db.conn() as c:
                if c.execute("SELECT 1 FROM seen_messages WHERE message_id = ?", (mid,)).fetchone():
                    continue
                c.execute("INSERT INTO seen_messages(message_id) VALUES (?)", (mid,))
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            try:
                row = service.ingest_email(subject, _text(msg), sender, source="inbox")
                results.append(row)
                print(f"recorded {row['direction']} {row['amount_minor'] / 100:,.2f} as {row['category']}")
            except ParseError as e:
                print(f"skipped '{subject[:50]}': {e}")
            except service.Duplicate:
                pass
    return results


if __name__ == "__main__":
    if "--watch" in sys.argv:
        while True:
            check_once()
            time.sleep(60)
    else:
        print(f"{len(check_once())} new transaction(s)")
