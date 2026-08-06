"""The adjustments directory: the user-owned CSVs holding every manual decision
made about the transactions — which category something belongs to today, and the
exceptions to those calls. They are inputs like the statement CSVs themselves;
the vault is rebuilt from them, never the other way around."""

import io
from pathlib import Path

import pandas as pd

from goblinvest_core.encryption import _MAGIC, _armor_bytes, _decrypt, _write_atomically

# The files that live in an adjustments directory, and the header each one gets.
# Users never type these names — they name the directory and we find the files.
_CATEGORIES_FILE = "categories.csv"
_RULES_FILE = "category_rules.csv"
_EXCEPTIONS_FILE = "category_exceptions.csv"

_FILES: dict[str, list[str]] = {
    _CATEGORIES_FILE: ["category"],
    _RULES_FILE: ["pattern", "category"],
    _EXCEPTIONS_FILE: ["account", "date", "description", "amount", "asset", "category"],
}


def _ensure_files(adjustments_dir: Path, *, encrypted: bool) -> None:
    """Create the folder (one level only, so a typo'd parent still raises) and
    any of its files that are not there yet. Existing files are never touched."""
    if adjustments_dir.exists() and not adjustments_dir.is_dir():
        raise NotADirectoryError(f"{adjustments_dir} is a file, not a folder")
    if not adjustments_dir.exists():
        # No parents=True: a typo'd path should fail, not build a tree.
        adjustments_dir.mkdir()
    for filename, columns in _FILES.items():
        filepath = adjustments_dir / filename
        if filepath.exists():
            continue
        # Armoring first means a refused or mistyped password leaves no file
        # behind; only the first one of these prompts, the rest reuse the key.
        header = (",".join(columns) + "\n").encode()
        _write_atomically(filepath, _armor_bytes(header, confirm=True) if encrypted else header)


def _folder_is_encrypted(adjustments_dir: Path) -> bool:
    """Whether the files already in a folder are encrypted, so files added
    later (a new kind of adjustment) match what is already there."""
    return any(
        (adjustments_dir / filename).is_file() and _is_armored(adjustments_dir / filename)
        for filename in _FILES
    )


def _is_armored(filepath: Path) -> bool:
    with filepath.open("rb") as f:
        return f.read(len(_MAGIC)) == _MAGIC


def _read_file(adjustments_dir: str | Path, filename: str, kind: str) -> tuple[pd.DataFrame, Path]:
    """Read one adjustments file (plain or encrypted); return it and its path."""
    filepath = Path(adjustments_dir).expanduser() / filename
    if not filepath.is_file():
        raise FileNotFoundError(
            f"No {kind} file at {filepath} (create_adjustments_files starts an empty one)"
        )
    source = io.BytesIO(_decrypt(filepath)) if _is_armored(filepath) else filepath
    # Everything is read as text so numeric-looking patterns and descriptions
    # survive verbatim; amount is converted back to float where it is matched.
    df = pd.read_csv(source, dtype=str)
    columns = _FILES[filename]
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{filepath} is missing the column(s): {', '.join(missing)}")
    return df[columns], filepath


def _write_file(adjustments_dir: str | Path, filename: str, df: pd.DataFrame) -> None:
    """Write one adjustments file back, staying encrypted if it was."""
    filepath = Path(adjustments_dir).expanduser() / filename
    data = df.to_csv(index=False).encode()
    if _is_armored(filepath):
        data = _armor_bytes(data, confirm=False)
    _write_atomically(filepath, data)
