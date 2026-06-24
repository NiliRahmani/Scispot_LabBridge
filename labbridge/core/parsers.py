"""File parsing + layout detection.

Phase 1 ships the dirty *long-format* parser (Variant B). The detector is written
so a grid/matrix parser can slot in later without changing the public API:

    detect_layout(df) -> "long" | "grid" | "unknown"
    load_table(path)  -> (raw_df, layout)

The parser stays intentionally dumb about *meaning*: it reads the file, strips
obvious junk, and hands a raw long DataFrame to the mapper. All header->canonical
decisions live in ``mapping.py``; all value cleaning lives in ``normalize.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pandas as pd

Layout = Literal["long", "grid", "unknown"]

_WELL_RE = re.compile(r"^\s*([A-Ha-h])\s*0?(1[0-2]|[1-9])\s*$")


def _looks_like_well(value: object) -> bool:
    return bool(_WELL_RE.match(str(value))) if value is not None else False


def detect_layout(df: pd.DataFrame) -> Layout:
    """Classify a freshly-read DataFrame as long vs grid.

    Heuristic:
    * A *grid* export has column headers that are mostly 1..12 and a first
      column whose values are mostly A..H.
    * A *long* export has one row per well: at least one column whose cells
      look like well coordinates (A1, H12, ...).
    """
    if df.empty:
        return "unknown"

    # grid signal: numeric-ish headers 1..12
    header_nums = sum(str(c).strip().isdigit() and 1 <= int(str(c).strip()) <= 12
                      for c in df.columns)
    if header_nums >= 8:
        return "grid"

    # long signal: any column that is mostly well coordinates
    for col in df.columns:
        series = df[col].dropna().astype(str).head(50)
        if len(series) == 0:
            continue
        hits = series.map(_looks_like_well).mean()
        if hits >= 0.7:
            return "long"

    return "unknown"


def _read_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        # Take the first sheet for the long-format case; multi-sheet handling
        # is a Phase-4 concern.
        return pd.read_excel(path, dtype=object)
    # CSV: keep everything as string so we don't lose "1,23" or "12.3 nM".
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def parse_long(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a long-format raw table: drop fully-empty rows/cols, trim headers."""
    out = df.copy()
    # normalize header whitespace but preserve original spelling for the mapper
    out.columns = [str(c).strip() for c in out.columns]
    # drop unnamed/empty columns that pandas sometimes appends
    keep = [c for c in out.columns if c and not c.lower().startswith("unnamed")]
    out = out[keep]
    # drop rows that are entirely blank
    def _row_blank(row) -> bool:
        return all(str(v).strip() == "" or pd.isna(v) for v in row)

    out = out[~out.apply(_row_blank, axis=1)].reset_index(drop=True)
    return out


def load_table(path: str | Path) -> tuple[pd.DataFrame, Layout]:
    """Read a file, detect its layout, and return a cleaned raw long DataFrame.

    Raises ``NotImplementedError`` for grid layout in Phase 1 (kept explicit so
    the UI/CLI can message the user rather than silently mis-parse).
    """
    path = Path(path)
    raw = _read_any(path)
    layout = detect_layout(raw)

    if layout == "grid":
        raise NotImplementedError(
            "Grid/matrix layout detected. The grid parser is a Phase-4 feature; "
            "Phase 1 handles long-format exports."
        )

    return parse_long(raw), layout
