#!/usr/bin/env python3
# load_vault.py
# =============
#
# End-to-end demo of goblinvest-core. Builds a fresh vault, defines four
# sample accounts and a small asset universe, loops over the CSV statements
# written by generate_fake_data.py and feeds them into add_transactions().
# Then pulls market prices from Yahoo Finance and prints both a current
# snapshot and a net-worth time series.
#
# Generate the statements first (their default location is gitignored):
#
#     uv run examples/load_transactions/generate_fake_data.py
#     uv run examples/load_transactions/load_vault.py
#
# By default the vault lands next to this script (gitignored, like the demo
# CSVs). Tools that consume the resulting database can put it anywhere — both
# locations can be overridden:
#
#     uv run examples/load_transactions/load_vault.py \
#         --data-dir /tmp/demo_statements --vault /tmp/PersonalFinanceVault.db
#
# Plaintext CSVs are used here so the focus stays on the loader; see the
# "Encrypted statement CSVs" section of the docs for keeping the same files
# encrypted on disk via encrypt_file / read_encrypted_file.

import argparse
import sys
from pathlib import Path

import pandas as pd

from goblinvest_core import Vault

HERE = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = HERE / "data"
DEFAULT_VAULT = HERE / "PersonalFinanceVault.db"

parser = argparse.ArgumentParser(
    description="Build a demo vault from the fake statement CSVs and summarize it."
)
parser.add_argument(
    "--data-dir",
    type=Path,
    default=DEFAULT_DATA_DIR,
    help=f"directory holding the statement CSVs (default: {DEFAULT_DATA_DIR})",
)
parser.add_argument(
    "--vault",
    type=Path,
    default=DEFAULT_VAULT,
    help=f"file path for the vault to create (default: {DEFAULT_VAULT})",
)
parser.add_argument(
    "--overwrite",
    action="store_true",
    help="replace an existing vault file without asking",
)
args = parser.parse_args()

data_dir = args.data_dir.expanduser()
vault_path = args.vault.expanduser()

if not data_dir.is_dir():
    sys.exit(
        f"No statement CSVs found at {data_dir}.\n"
        "Run generate_fake_data.py first (or point --data-dir at them)."
    )

if vault_path.exists() and not args.overwrite:
    answer = input(f"A file already exists at {vault_path}. Overwrite? [y/N]: ")
    if answer.strip().lower() not in ("y", "yes"):
        sys.exit("Aborting.")

v = Vault.create(vault_path, overwrite=True)

# ---------------------------------------------------------------------------
# Accounts. The joint account has ownership_share=0.5, which makes
# summarize_accounts attribute only half of its balance to the user.
# ---------------------------------------------------------------------------
v.add_account("checking", account_group_name="cash")
v.add_account("joint-checking", ownership_share=0.5, account_group_name="cash")
v.add_account("credit-card", account_group_name="credit")
v.add_account("brokerage", account_group_name="investments")

# ---------------------------------------------------------------------------
# Asset universe. USD is created with the vault itself; everything else
# has to be registered before transactions can reference it.
# ---------------------------------------------------------------------------
for ticker in ("VFIAX", "VBTLX", "AAPL"):
    v.add_asset(ticker)


# ---------------------------------------------------------------------------
# Loaders. Bank-style accounts ship CSVs with (date, description, amount);
# the brokerage CSVs have (date, description, units, asset). In both cases
# the columns are handed straight to add_transactions() and any upsert /
# dedupe behavior is the vault's responsibility.
# ---------------------------------------------------------------------------
def load_bank(v, account):
    for path in sorted((data_dir / account).glob("*.csv")):
        df = pd.read_csv(path)
        v.add_transactions(account, df["date"], df["description"], df["amount"])


def load_brokerage(v, account):
    for path in sorted((data_dir / account).glob("*.csv")):
        df = pd.read_csv(path)
        v.add_transactions(account, df["date"], df["description"], df["units"], assets=df["asset"])


load_bank(v, "checking")
load_bank(v, "joint-checking")
load_bank(v, "credit-card")
load_brokerage(v, "brokerage")

# Pull historical market prices for everything we hold. The vault uses
# them to value share positions in the summaries below.
v.populate_yfinance_prices(["VFIAX", "VBTLX", "AAPL"])

# ---------------------------------------------------------------------------
# Summaries.
# ---------------------------------------------------------------------------
print("\n=== current snapshot (summarize_accounts) ===")
print(v.summarize_accounts().to_string(index=False))

print("\n=== daily net worth (head and tail) ===")
net_worth = v.accumulate_mv().sum(axis=1).round(2).rename("net_worth")
print(net_worth.head(5).to_string())
print("  ...")
print(net_worth.tail(5).to_string())

v.close()
print(f"\nVault written to: {vault_path}")
