"""SQLite storage. Amounts are stored in minor units (kobo/cents) as integers."""
import os
import sqlite3
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint TEXT UNIQUE NOT NULL,
  reference TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
  currency TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('credit','debit')),
  counterparty TEXT NOT NULL DEFAULT 'Unspecified',
  narration TEXT NOT NULL DEFAULT '',
  balance_after_minor INTEGER,
  bank TEXT NOT NULL DEFAULT 'Unknown',
  account_masked TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  category TEXT NOT NULL,
  subcategory TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL,
  classifier TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('posted','review')),
  ai_model TEXT NOT NULL DEFAULT '',
  ai_chat_id TEXT NOT NULL DEFAULT '',
  ai_proof TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
  account TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('debit','credit')),
  amount_minor INTEGER NOT NULL CHECK (amount_minor > 0)
);
CREATE TABLE IF NOT EXISTS anchors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  period TEXT NOT NULL,
  books_root TEXT NOT NULL,
  report_hash TEXT NOT NULL,
  tx_count INTEGER NOT NULL,
  near_tx TEXT NOT NULL DEFAULT '',
  contract TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS seen_messages (message_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS subscribers (chat_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL DEFAULT (datetime('now')));
"""


def init(path: str | None = None) -> None:
    p = path or settings.db_path
    if os.path.dirname(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    with conn(p) as c:
        c.executescript(SCHEMA)


@contextmanager
def conn(path: str | None = None):
    c = sqlite3.connect(path or settings.db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
