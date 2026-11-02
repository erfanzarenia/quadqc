"""Tabular input and output helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .config import ConfigError

NA = "n/a"


def read_table(path: Path, id_column: str, required: tuple[str, ...] = ()) -> pd.DataFrame:
    """Read a TSV/CSV as text, with its ID column renamed to participant_id."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"table not found: {path}")
    sep = "," if path.suffix.lower() == ".csv" else "\t"
    table = pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False)
    missing = [c for c in (id_column, *required) if c not in table.columns]
    if missing:
        raise ConfigError(f"{path}: missing column(s) {missing}; found {list(table.columns)}")
    table = table.rename(columns={id_column: "participant_id"})
    table["participant_id"] = table["participant_id"].map(normalise_id)
    if (table["participant_id"] == "sub-").any():
        raise ConfigError(f"{path}: empty participant IDs in column '{id_column}'")
    return table


def normalise_id(value: str) -> str:
    """BIDS participant label; bare labels gain the sub- prefix."""
    value = str(value).strip()
    return value if value.startswith("sub-") else f"sub-{value}"


def to_numeric(table: pd.DataFrame, keys: tuple[str, ...]) -> tuple[pd.DataFrame, list[str]]:
    """Coerce metric columns to numbers; return the columns that hold no numbers at all."""
    out = table[list(keys)].copy()
    dropped = []
    for column in table.columns:
        if column in keys:
            continue
        text = table[column].replace({NA: None, "": None, "NA": None, "nan": None, "NaN": None})
        values = pd.to_numeric(text, errors="coerce")
        if values.notna().any() or text.isna().all():
            out[column] = values
        else:
            dropped.append(column)
    return out, dropped


def write_tsv(table: pd.DataFrame, path: Path) -> None:
    table.to_csv(path, sep="\t", index=False, na_rep=NA, lineterminator="\n")


def resolve_id_keys(table: pd.DataFrame) -> tuple[str, ...]:
    return ("participant_id", "session_id") if "session_id" in table.columns else (
        "participant_id",)


def first_duplicate(table: pd.DataFrame, keys: tuple[str, ...]) -> Optional[str]:
    dup = table.duplicated(list(keys))
    return None if not dup.any() else str(table.loc[dup, list(keys)].iloc[0].tolist())
