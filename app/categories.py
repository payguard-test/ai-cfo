"""Chart of accounts and the categories the agent may assign.

Each category says which bank direction it is valid for and which ledger account
the other side of Cash goes to. A credit is never automatically "sales": owner
capital, loans, refunds received and transfers from the owner's other accounts are
separate, and they land in different financial statements.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    direction: str  # credit | debit
    account: str  # ledger account opposite Cash
    section: str  # where it shows up: income, cogs, expense, asset, liability, equity, transfer
    cash_flow: str  # operating | investing | financing | internal


CATEGORIES: dict[str, Category] = {c.key: c for c in [
    # money in
    Category("sales", "Sales revenue", "credit", "Sales Revenue", "income", "operating"),
    Category("other_income", "Other income", "credit", "Other Income", "income", "operating"),
    Category("capital", "Owner capital", "credit", "Owner Capital", "equity", "financing"),
    Category("loan_received", "Loan received", "credit", "Loans Payable", "liability", "financing"),
    Category("refund_received", "Refund from supplier", "credit", "Supplier Refunds", "expense_contra", "operating"),
    Category("transfer_in", "Transfer from own account", "credit", "Other Bank Accounts", "transfer", "internal"),
    Category("uncategorized_in", "Needs review (money in)", "credit", "Suspense", "suspense", "operating"),
    # money out
    Category("cost_of_sales", "Stock and ingredients", "debit", "Cost of Sales", "cogs", "operating"),
    Category("rent", "Rent", "debit", "Rent Expense", "expense", "operating"),
    Category("salaries", "Salaries and wages", "debit", "Salaries Expense", "expense", "operating"),
    Category("utilities", "Fuel, power and utilities", "debit", "Utilities Expense", "expense", "operating"),
    Category("marketing", "Marketing", "debit", "Marketing Expense", "expense", "operating"),
    Category("transport", "Transport and logistics", "debit", "Transport Expense", "expense", "operating"),
    Category("bank_charges", "Bank charges", "debit", "Bank Charges", "expense", "operating"),
    Category("taxes", "Taxes and levies", "debit", "Taxes Expense", "expense", "operating"),
    Category("other_expense", "Other operating expense", "debit", "Other Expenses", "expense", "operating"),
    Category("refund_paid", "Refund to customer", "debit", "Sales Returns", "income_contra", "operating"),
    Category("asset_purchase", "Equipment and assets", "debit", "Fixed Assets", "asset", "investing"),
    Category("loan_repayment", "Loan repayment", "debit", "Loans Payable", "liability", "financing"),
    Category("owner_drawings", "Owner drawings", "debit", "Owner Drawings", "equity", "financing"),
    Category("transfer_out", "Transfer to own account", "debit", "Other Bank Accounts", "transfer", "internal"),
    Category("uncategorized_out", "Needs review (money out)", "debit", "Suspense", "suspense", "operating"),
]}

UNCATEGORIZED = {"credit": "uncategorized_in", "debit": "uncategorized_out"}


def allowed(direction: str) -> list[Category]:
    return [c for c in CATEGORIES.values() if c.direction == direction]
