"""Categories: the names you define, and the rules and exceptions that hand them
out so every transaction has exactly one. They are read from the adjustments
directory (see `adjustments.py`) and written into the vault, never the other way
around."""

import re
from pathlib import Path

import pandas as pd

from goblinvest_core.adjustments import _read_file

# What a transaction with no category reads as, everywhere. Reserved: it means
# "no category", so the files may not use it as a category name.
UNCLASSIFIED = "unclassified"

# add_transactions keeps identical same-day transactions apart by suffixing
# their descriptions with " (2)", " (3)", ...; categories treat those as the
# same description, so a rule for "Netflix" also covers "Netflix (2)".
_DUP_SUFFIX = re.compile(r"\s\(\d+\)$")


def _canonical_categories(defined: pd.DataFrame) -> dict[str, str]:
    """Map every defined category's lowercase form to the spelling you defined."""
    return dict(zip(defined["category"].str.lower(), defined["category"]))


def _resolve_categories(
    values: pd.Series, canonical: dict[str, str], *, source: str | None = None
) -> pd.Series:
    """Replace category names with the spelling they were defined under.
    Anything not defined raises, naming every offender at once. Missing values
    (an exception being removed) pass through untouched."""
    resolved = pd.Series(pd.NA, index=values.index, dtype=object)
    given = values.notna()
    resolved[given] = values[given].astype(str).str.strip().str.lower().map(canonical)

    unknown = given & resolved.isna()
    if unknown.any():
        missing = sorted(set(values[unknown].astype(str).str.strip()))
        where = f" in {source}" if source else ""
        raise ValueError(
            f"These categories are not defined{where}: {', '.join(missing)}. "
            "Define them with add_category first (or fix the spelling)."
        )
    return resolved


def _strip_dup_suffix(descriptions: pd.Series) -> pd.Series:
    """Remove the " (2)"-style same-day duplicate suffix, if present."""
    return descriptions.str.replace(_DUP_SUFFIX, "", regex=True)


def _normalize_desc(descriptions: pd.Series) -> pd.Series:
    """The form descriptions are matched in: suffix-stripped, trimmed, lowercased."""
    return _strip_dup_suffix(descriptions).str.strip().str.lower()


def _read_category_file(
    adjustments_dir: str | Path, filename: str, kind: str
) -> tuple[pd.DataFrame, Path]:
    """Read a rules or exceptions file and validate its category column."""
    df, filepath = _read_file(adjustments_dir, filename, kind)

    bad = df["category"].isna() | (df["category"].str.strip() == "")
    if bad.any():
        raise ValueError(f"{filepath} has {int(bad.sum())} row(s) with an empty category")
    reserved = df["category"].str.strip().str.lower() == UNCLASSIFIED
    if reserved.any():
        raise ValueError(
            f'{filepath} uses the reserved category name "{UNCLASSIFIED}": it means '
            "no category, so it cannot be assigned. Remove those rows instead."
        )
    df["category"] = df["category"].str.strip()
    return df, filepath
