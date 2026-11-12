"""Double-entry posting and financial statements (cash basis, from the bank feed).

Every transaction posts exactly two balanced lines against Cash. Statements are
built from the journal, so the balance sheet always balances:
  Assets (Cash, Other Bank Accounts, Fixed Assets, Suspense)
    = Liabilities (Loans Payable) + Equity (Capital - Drawings + Retained Earnings)
Limits, stated in every report: cash basis, no accruals, no depreciation, no
inventory count; not audited or tax-ready.
"""
import sqlite3

from .categories import CATEGORIES

CASH = "Cash at Bank"
NOTE = "Cash-basis statements built from bank alerts. No accruals, depreciation or stock count. Not audited or tax-ready."


def journal_lines(direction: str, category: str, amount_minor: int) -> list[tuple[str, str, int]]:
    other = CATEGORIES[category].account
    if direction == "credit":
        return [(CASH, "debit", amount_minor), (other, "credit", amount_minor)]
    return [(other, "debit", amount_minor), (CASH, "credit", amount_minor)]


def post(c: sqlite3.Connection, tx_id: int, direction: str, category: str, amount_minor: int) -> None:
    c.execute("DELETE FROM journal WHERE transaction_id = ?", (tx_id,))
    for account, side, amt in journal_lines(direction, category, amount_minor):
        c.execute("INSERT INTO journal(transaction_id, account, side, amount_minor) VALUES (?,?,?,?)", (tx_id, account, side, amt))


def _period_filter(period: str) -> tuple[str, tuple]:
    if not period or period == "all":
        return "", ()
    return " AND substr(t.occurred_at, 1, 7) = ?", (period,)


def balances(c: sqlite3.Connection, period: str = "all") -> dict[str, int]:
    """Net debit balance per account (debits positive, credits negative)."""
    where, args = _period_filter(period)
    rows = c.execute(
        f"""SELECT j.account, SUM(CASE WHEN j.side='debit' THEN j.amount_minor ELSE -j.amount_minor END) AS bal
            FROM journal j JOIN transactions t ON t.id = j.transaction_id WHERE 1=1 {where} GROUP BY j.account""",
        args,
    ).fetchall()
    return {r["account"]: r["bal"] for r in rows}


def by_category(c: sqlite3.Connection, period: str = "all") -> dict[str, int]:
    where, args = _period_filter(period)
    rows = c.execute(f"SELECT category, SUM(amount_minor) s FROM transactions t WHERE 1=1 {where} GROUP BY category", args).fetchall()
    return {r["category"]: r["s"] for r in rows}


def statements(c: sqlite3.Connection, period: str = "all") -> dict:
    cat = by_category(c, period)
    g = lambda k: cat.get(k, 0)  # noqa: E731
    revenue = g("sales") - g("refund_paid")
    cogs = g("cost_of_sales") - g("refund_received")
    gross = revenue - cogs
    opex = {CATEGORIES[k].label: g(k) for k in ("rent", "salaries", "utilities", "marketing", "transport", "bank_charges", "taxes", "other_expense") if g(k)}
    total_opex = sum(opex.values())
    other_income = g("other_income")
    net = gross - total_opex + other_income
    income_statement = {
        "revenue": {"Sales": g("sales"), "Less: refunds to customers": -g("refund_paid"), "Net revenue": revenue},
        "cost_of_sales": {"Stock and ingredients": g("cost_of_sales"), "Less: supplier refunds": -g("refund_received"), "Total": cogs},
        "gross_profit": gross,
        "operating_expenses": opex,
        "total_operating_expenses": total_opex,
        "other_income": other_income,
        "net_profit": net,
        "gross_margin_pct": round(gross * 100 / revenue, 1) if revenue else None,
    }

    # Balance sheet is cumulative up to the end of the period, so use all history up to it.
    bal = balances(c, "all") if period == "all" else _balances_to(c, period)
    cash = bal.get(CASH, 0)
    other_bank = bal.get("Other Bank Accounts", 0)
    fixed = bal.get("Fixed Assets", 0)
    suspense = bal.get("Suspense", 0)
    loans = -bal.get("Loans Payable", 0)
    capital = -bal.get("Owner Capital", 0)
    drawings = bal.get("Owner Drawings", 0)
    pl_accounts = [a.account for a in CATEGORIES.values() if a.section in ("income", "income_contra", "cogs", "expense", "expense_contra")]
    retained = -sum(bal.get(a, 0) for a in set(pl_accounts))
    assets = {"Cash at bank": cash, "Other bank accounts": other_bank, "Equipment and assets (at cost)": fixed}
    liabilities = {"Loans payable": loans}
    if suspense > 0:
        assets["Unclassified money out (needs review)"] = suspense
    elif suspense < 0:
        liabilities["Unclassified money in (needs review)"] = -suspense
    total_assets = sum(assets.values())
    equity = {"Owner capital": capital, "Less: owner drawings": -drawings, "Retained earnings": retained}
    total_le = sum(liabilities.values()) + sum(equity.values())
    balance_sheet = {
        "assets": assets,
        "total_assets": total_assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_liabilities_and_equity": total_le,
        "balances": total_assets == total_le,
    }

    op_in = g("sales") + g("other_income") + g("refund_received")
    op_out = g("cost_of_sales") + total_opex + g("refund_paid")
    cash_flow = {
        "operating": {"Cash from customers and other income": op_in, "Cash paid to suppliers, staff and expenses": -op_out, "Net operating": op_in - op_out},
        "investing": {"Equipment and assets bought": -g("asset_purchase"), "Net investing": -g("asset_purchase")},
        "financing": {
            "Owner capital in": g("capital"),
            "Loans received": g("loan_received"),
            "Loan repayments": -g("loan_repayment"),
            "Owner drawings": -g("owner_drawings"),
            "Net financing": g("capital") + g("loan_received") - g("loan_repayment") - g("owner_drawings"),
        },
        "internal_transfers": {"In": g("transfer_in"), "Out": -g("transfer_out")},
    }
    net_change = (op_in - op_out) - g("asset_purchase") + cash_flow["financing"]["Net financing"] + g("transfer_in") - g("transfer_out") + g("uncategorized_in") - g("uncategorized_out")
    cash_flow["net_change_in_cash"] = net_change

    capital_summary = {
        "Owner capital invested": g("capital"),
        "Owner drawings": g("owner_drawings"),
        "Net owner investment": g("capital") - g("owner_drawings"),
        "Loans outstanding": loans,
        "Equity (capital - drawings + retained earnings)": sum(equity.values()),
    }
    return {
        "period": period or "all",
        "income_statement": income_statement,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "capital": capital_summary,
        "note": NOTE,
    }


def _balances_to(c: sqlite3.Connection, period: str) -> dict[str, int]:
    rows = c.execute(
        """SELECT j.account, SUM(CASE WHEN j.side='debit' THEN j.amount_minor ELSE -j.amount_minor END) AS bal
           FROM journal j JOIN transactions t ON t.id = j.transaction_id
           WHERE substr(t.occurred_at, 1, 7) <= ? GROUP BY j.account""",
        (period,),
    ).fetchall()
    return {r["account"]: r["bal"] for r in rows}


def trial_balance(c: sqlite3.Connection, period: str = "all") -> dict:
    bal = balances(c, period)
    rows = [{"account": a, "debit": b if b > 0 else 0, "credit": -b if b < 0 else 0} for a, b in sorted(bal.items())]
    td, tc = sum(r["debit"] for r in rows), sum(r["credit"] for r in rows)
    return {"period": period or "all", "accounts": rows, "total_debit": td, "total_credit": tc, "balanced": td == tc}
