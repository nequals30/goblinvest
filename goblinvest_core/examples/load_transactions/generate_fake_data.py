#!/usr/bin/env python3
# generate_fake_data.py
# =====================
#
# One-shot script that emits realistic fake CSV statements. The output is not
# committed to git (statement CSVs, even fake ones, don't belong in a code
# repository), so run this once before trying load_vault.py:
#
#     uv run examples/load_transactions/generate_fake_data.py
#     uv run examples/load_transactions/generate_fake_data.py --data-dir /tmp/demo_statements
#
# By default the CSVs land in `data/` next to this script (gitignored), which
# is where load_vault.py looks for them. Pass --data-dir to write elsewhere.
#
# The persona is a US-based 30-something with a salaried job, a partner they
# share a checking account with, a credit card, and a self-directed brokerage
# holding VFIAX, VBTLX and AAPL. Transactions span 2023-01-01 through
# 2026-05-31, with monthly transfers between checking and the brokerage (and
# between checking and the joint account) so cross-account flows are
# observable in the loaded data.

import argparse
import calendar
import datetime
import random
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "data"
YEARS = range(2023, 2027)
END_DATE = datetime.date(2026, 5, 31)
ACCOUNTS = ("credit-card", "checking", "joint-checking", "brokerage")

# Deterministic so the output bytes are stable across regenerations.
RNG = random.Random(20260101)


# ---------------------------------------------------------------------------
# Rough prices used only when converting a dollar amount into a plausible
# share count for buy transactions. Real prices come from Yahoo Finance after
# loading; these just keep the generated share quantities visually sane.
# ---------------------------------------------------------------------------
def approx_price(ticker: str, dt: datetime.date) -> float:
    import math

    yrs = (dt - datetime.date(2023, 1, 1)).days / 365.0
    if ticker == "VFIAX":
        return 355.0 + 75.0 * yrs
    if ticker == "VBTLX":
        return 9.60 + 0.40 * math.sin(yrs * 2 * math.pi)
    if ticker == "AAPL":
        return 140.0 + 30.0 * yrs
    return 1.0


def months_in_range():
    return [
        (y, m) for y in YEARS for m in range(1, 13) if datetime.date(y, m, 1) <= END_DATE
    ]


# Previous calendar month, used to pay a credit-card statement one month in
# arrears (you pay last month's charges, not the current month's).
def prev_month(y, m):
    return (y - 1, 12) if m == 1 else (y, m - 1)


# ---------------------------------------------------------------------------
# Per-account ledger builders. Each returns a list of row dicts.
# Bank-style accounts produce (date, description, amount); the brokerage
# produces (date, description, units, asset).
# ---------------------------------------------------------------------------
def gen_credit_card():
    merchants = [
        ("Shell Gas Station", 45.0, 15.0),
        ("BP Gas", 42.0, 15.0),
        ("Chipotle", 13.0, 4.0),
        ("Whole Foods Market", 68.0, 25.0),
        ("Amazon.com", 42.0, 30.0),
        ("Spotify Premium", 10.99, 0.0),
        ("Netflix", 15.99, 0.0),
        ("Steam", 25.0, 20.0),
        ("REI Co-op", 85.0, 50.0),
        ("Blue Bottle Coffee", 6.5, 2.0),
        ("Uber Trip", 18.0, 10.0),
        ("Target", 55.0, 30.0),
        ("Best Buy", 145.0, 60.0),
        ("Walgreens", 22.0, 12.0),
    ]
    rows = []
    monthly_totals = {}
    for y, m in months_in_range():
        ndays = calendar.monthrange(y, m)[1]
        monthly_total = 0.0
        n = 6 + RNG.randint(0, 2)
        for _ in range(n):
            d = RNG.randint(1, min(ndays, 24))
            dt = datetime.date(y, m, d)
            if dt > END_DATE:
                continue
            name, base, spread = RNG.choice(merchants)
            amt = -round(base + (RNG.random() - 0.5) * 2 * spread, 2)
            rows.append({"date": dt, "description": name, "amount": amt})
            monthly_total += -amt
        monthly_totals[(y, m)] = round(monthly_total, 2)
    # Statement paid in full from checking on the 25th, one month in arrears:
    # on month M we pay month M-1's charges. The first month has no prior
    # statement, and the final month's charges stay unpaid, so the card ends
    # the period with a small carried (negative) balance.
    for y, m in months_in_range():
        pay_dt = datetime.date(y, m, 25)
        if pay_dt > END_DATE:
            continue
        bal = monthly_totals.get(prev_month(y, m), 0.0)
        if bal > 0:
            rows.append({"date": pay_dt, "description": "Payment from checking", "amount": bal})
    rows.sort(key=lambda r: (r["date"], r["description"]))
    return rows, monthly_totals


def gen_checking(cc_monthly):
    rows = []
    for y, m in months_in_range():
        # Bi-monthly paychecks on the 1st and 15th.
        for d in (1, 15):
            dt = datetime.date(y, m, d)
            if dt > END_DATE:
                continue
            rows.append(
                {
                    "date": dt,
                    "description": "ACME Corp Payroll",
                    "amount": round(2950 + 100 * RNG.random(), 2),
                }
            )
        dt = datetime.date(y, m, 5)
        if dt <= END_DATE:
            rows.append(
                {"date": dt, "description": "Transfer to joint-checking", "amount": -1400.00}
            )

        dt = datetime.date(y, m, 16)
        if dt <= END_DATE:
            rows.append({"date": dt, "description": "Transfer to brokerage", "amount": -4000.00})

        dt = datetime.date(y, m, 20)
        if dt <= END_DATE:
            rows.append(
                {
                    "date": dt,
                    "description": "ATM withdrawal",
                    "amount": -round(60 + 20 * RNG.random(), 2),
                }
            )

        # Pay off last month's credit card statement on the 25th (mirrors the
        # card's one-month-arrears payment in gen_credit_card).
        dt = datetime.date(y, m, 25)
        bal = cc_monthly.get(prev_month(y, m), 0.0)
        if dt <= END_DATE and bal > 0:
            rows.append({"date": dt, "description": "Credit card payment", "amount": -bal})
    # Annual tax refund in April.
    for y in YEARS:
        dt = datetime.date(y, 4, 18)
        if dt > END_DATE:
            continue
        rows.append(
            {
                "date": dt,
                "description": "IRS tax refund",
                "amount": round(700 + 400 * RNG.random(), 2),
            }
        )
    rows.sort(key=lambda r: (r["date"], r["description"]))
    return rows


def gen_joint():
    rows = []
    # Starting balance so the account has a cushion for rent (posted on the
    # 1st) before the partners' contributions arrive on the 5th; without it
    # the balance dips negative early each month until the buffer builds up.
    rows.append(
        {"date": datetime.date(2023, 1, 1), "description": "Opening balance", "amount": 3000.00}
    )
    for y, m in months_in_range():
        # Rent on the 1st.
        dt = datetime.date(y, m, 1)
        if dt <= END_DATE:
            rows.append(
                {"date": dt, "description": "Rent - 24th Street Apartment", "amount": -2000.00}
            )

        # Contributions from both partners on the 5th.
        dt = datetime.date(y, m, 5)
        if dt <= END_DATE:
            rows.append(
                {"date": dt, "description": "Contribution from checking", "amount": 1400.00}
            )
            rows.append(
                {"date": dt, "description": "Contribution from partner", "amount": 1400.00}
            )

        # Utilities on the 10th.
        dt = datetime.date(y, m, 10)
        if dt <= END_DATE:
            rows.append(
                {
                    "date": dt,
                    "description": "ConEd electric",
                    "amount": -round(70 + 30 * RNG.random(), 2),
                }
            )
            rows.append({"date": dt, "description": "Verizon internet", "amount": -79.99})

        # Weekly-ish groceries.
        for d in (7, 14, 21, 28):
            dt = datetime.date(y, m, d)
            if dt > END_DATE:
                continue
            rows.append(
                {
                    "date": dt,
                    "description": "Trader Joe's",
                    "amount": -round(80 + 50 * RNG.random(), 2),
                }
            )

        # Monthly date night.
        dt = datetime.date(y, m, 17)
        if dt <= END_DATE:
            rows.append(
                {
                    "date": dt,
                    "description": "Sushi Date Night",
                    "amount": -round(75 + 25 * RNG.random(), 2),
                }
            )
    rows.sort(key=lambda r: (r["date"], r["description"]))
    return rows


def gen_brokerage():
    rows = []
    for y, m in months_in_range():
        # Cash transfer in - mirrors checking's outflow on the 16th.
        dt = datetime.date(y, m, 16)
        if dt <= END_DATE:
            rows.append(
                {
                    "date": dt,
                    "description": "Transfer from checking",
                    "units": 4000.00,
                    "asset": "USD",
                }
            )

        # 80/20 buy split on the 17th. Slightly less than the cash inflow
        # so the account accumulates a small free-cash buffer for the
        # occasional AAPL purchase.
        dt = datetime.date(y, m, 17)
        if dt <= END_DATE:
            vf_dollars = 3000.00
            vb_dollars = 700.00
            vf_shares = round(vf_dollars / approx_price("VFIAX", dt), 4)
            vb_shares = round(vb_dollars / approx_price("VBTLX", dt), 4)
            rows.append(
                {"date": dt, "description": "Buy VFIAX", "units": -vf_dollars, "asset": "USD"}
            )
            rows.append(
                {"date": dt, "description": "Buy VFIAX", "units": vf_shares, "asset": "VFIAX"}
            )
            rows.append(
                {"date": dt, "description": "Buy VBTLX", "units": -vb_dollars, "asset": "USD"}
            )
            rows.append(
                {"date": dt, "description": "Buy VBTLX", "units": vb_shares, "asset": "VBTLX"}
            )

    # Quarterly VFIAX dividends, paid as cash.
    for y in YEARS:
        for m in (3, 6, 9, 12):
            dt = datetime.date(y, m, calendar.monthrange(y, m)[1])
            if dt > END_DATE:
                continue
            rows.append(
                {
                    "date": dt,
                    "description": "VFIAX quarterly dividend",
                    "units": round(8 + 6 * RNG.random(), 2),
                    "asset": "USD",
                }
            )

    # One AAPL buy each May.
    for y in YEARS:
        dt = datetime.date(y, 5, 20)
        if dt > END_DATE:
            continue
        dollars = 500.00
        shares = round(dollars / approx_price("AAPL", dt), 4)
        rows.append({"date": dt, "description": "Buy AAPL", "units": -dollars, "asset": "USD"})
        rows.append({"date": dt, "description": "Buy AAPL", "units": shares, "asset": "AAPL"})

    rows.sort(key=lambda r: (r["date"], r["description"]))
    return rows


# ---------------------------------------------------------------------------
# Output: split each ledger by year and write one CSV per account-year.
# ---------------------------------------------------------------------------
def write_year(data_dir: Path, account: str, year: int, rows):
    yr_rows = [r for r in rows if r["date"].year == year]
    if not yr_rows:
        return
    subdir = data_dir / account
    subdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(yr_rows).to_csv(subdir / f"{year}.csv", index=False)


def main():
    parser = argparse.ArgumentParser(
        description="Write fake demo statement CSVs, one folder per account."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"directory to write the CSVs into (default: {DEFAULT_DATA_DIR})",
    )
    data_dir = parser.parse_args().data_dir.expanduser()

    # Wipe prior demo files (only the account subfolders this script owns) so
    # the regenerated set is the only one.
    for account in ACCOUNTS:
        for path in (data_dir / account).glob("*.csv"):
            path.unlink()

    cc_rows, cc_monthly = gen_credit_card()
    checking_rows = gen_checking(cc_monthly)
    joint_rows = gen_joint()
    brokerage_rows = gen_brokerage()

    for y in YEARS:
        write_year(data_dir, "credit-card", y, cc_rows)
        write_year(data_dir, "checking", y, checking_rows)
        write_year(data_dir, "joint-checking", y, joint_rows)
        write_year(data_dir, "brokerage", y, brokerage_rows)

    print(f"\nWrote demo statements under: {data_dir}")


if __name__ == "__main__":
    main()
