"""Value normalization: units, decimals, dates, control types, well coords.

Operates on a canonical-renamed DataFrame (output of ``mapping.rename_to_canonical``)
and records every change to an :class:`~labbridge.core.audit.AuditLog`. Missing /
overflow / non-numeric values are *not* invented here -- they become NaN and are
flagged later by ``qc``. Normalization only standardizes things that are
genuinely recoverable (a unit, a decimal comma, a date format).
"""

from __future__ import annotations

import re
from typing import Optional

import numpy as np
import pandas as pd
from dateutil import parser as dparser

from .audit import AuditLog
from .schema import (
    CONCENTRATION_TO_NM,
    CANONICAL_CONCENTRATION_UNIT,
    MISSING_SENTINELS,
    OVERFLOW_TOKENS,
    canonical_control_type,
)

_UNIT_IN_VALUE_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([a-zA-Zµμ%/]+)?\s*$")
_WELL_RE = re.compile(r"^\s*([A-Ha-h])\s*0?(1[0-2]|[1-9])\s*$")


def _well_of(row: pd.Series) -> Optional[str]:
    w = row.get("well_position")
    return None if w is None or (isinstance(w, float) and np.isnan(w)) else str(w)


def normalize_well(value: object) -> Optional[str]:
    m = _WELL_RE.match(str(value)) if value is not None else None
    if not m:
        return None
    return f"{m.group(1).upper()}{int(m.group(2))}"


def _parse_numeric_token(raw: str) -> tuple[Optional[float], Optional[str], list[str]]:
    """Return (value, embedded_unit, transforms) from a raw value string.

    transforms is a list of human-readable change descriptions (decimal comma,
    stripped unit). Returns (None, None, [...]) for missing/overflow/garbage.
    """
    s = str(raw).strip()
    transforms: list[str] = []
    if s.lower() in MISSING_SENTINELS:
        return None, None, ["missing"]
    if s.lower() in OVERFLOW_TOKENS:
        return None, None, ["overflow"]

    m = _UNIT_IN_VALUE_RE.match(s)
    if not m:
        return None, None, ["unparseable"]

    num_str, unit = m.group(1), m.group(2)
    if "," in num_str and "." not in num_str:
        num_str = num_str.replace(",", ".")
        transforms.append("decimal_comma")
    try:
        val = float(num_str)
    except ValueError:
        return None, None, ["unparseable"]

    if unit:
        transforms.append("embedded_unit")
    return val, unit, transforms


def _to_nm(value: float, unit: Optional[str]) -> tuple[float, bool]:
    """Convert a concentration to nM. Returns (value_nM, converted?)."""
    if not unit:
        return value, False
    key = unit.strip().lower()
    factor = CONCENTRATION_TO_NM.get(key)
    if factor is None or factor == 1.0:
        return value, False
    return value * factor, True


def normalize_frame(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Normalize a canonical-renamed frame in place-ish (returns a new frame)."""
    out = df.copy()

    # --- well positions ---
    if "well_position" in out.columns:
        new_wells = []
        for _, row in out.iterrows():
            raw = row["well_position"]
            norm = normalize_well(raw)
            if norm is not None and norm != str(raw).strip():
                audit.normalized("well_position", norm, raw, norm, "standardized well coord")
            new_wells.append(norm if norm is not None else raw)
        out["well_position"] = new_wells

    # --- control types ---
    if "control_type" in out.columns:
        new_types = []
        for _, row in out.iterrows():
            raw = row["control_type"]
            canon = canonical_control_type(raw)
            well = _well_of(row)
            if canon is not None and canon != str(raw).strip():
                audit.normalized("control_type", well, raw, canon, "standardized control type")
                new_types.append(canon)
            elif canon is None:
                new_types.append(raw)  # leave; qc may flag unknown roles
            else:
                new_types.append(canon)
        out["control_type"] = new_types

    # --- measurement value + unit (the interesting one) ---
    if "measurement_value" in out.columns:
        unit_col = out["measurement_unit"] if "measurement_unit" in out.columns else None
        new_vals, new_units = [], []
        for idx, row in out.iterrows():
            well = _well_of(row)
            raw = row["measurement_value"]
            declared_unit = (str(unit_col.iloc[idx]).strip()
                             if unit_col is not None and pd.notna(unit_col.iloc[idx]) else None)

            val, embedded_unit, transforms = _parse_numeric_token(raw)
            unit = embedded_unit or declared_unit

            if val is None:
                # value not recoverable -> NaN, and flag the reason now (we can
                # see the raw token here; qc only sees the NaN).
                if "missing" in transforms:
                    audit.flag("MISSING_VALUE", well, severity="warn",
                               detail=f"empty/sentinel value '{raw}'",
                               field_name="measurement_value")
                elif "overflow" in transforms:
                    audit.flag("SATURATED_OR_OVERFLOW", well, severity="error",
                               detail=f"detector overflow token '{raw}'",
                               field_name="measurement_value")
                else:
                    audit.flag("VALUE_UNPARSEABLE", well, severity="error",
                               detail=f"could not parse value '{raw}'",
                               field_name="measurement_value")
                new_vals.append(np.nan)
                new_units.append(CANONICAL_CONCENTRATION_UNIT if unit is None else unit)
                continue

            if "decimal_comma" in transforms:
                audit.normalized("measurement_value", well, raw, val, "decimal comma -> point",
                                 rule="DECIMAL_NORMALIZED")
            if "embedded_unit" in transforms:
                audit.normalized("measurement_value", well, raw, val,
                                 f"stripped embedded unit '{embedded_unit}'",
                                 rule="EMBEDDED_UNIT_STRIPPED")

            val_nm, converted = _to_nm(val, unit)
            if converted:
                audit.normalized("measurement_value", well, f"{val} {unit}",
                                 f"{val_nm} {CANONICAL_CONCENTRATION_UNIT}",
                                 f"converted {unit} -> {CANONICAL_CONCENTRATION_UNIT}",
                                 rule="UNIT_NORMALIZED")
                unit = CANONICAL_CONCENTRATION_UNIT

            new_vals.append(round(val_nm, 6))
            new_units.append(unit or CANONICAL_CONCENTRATION_UNIT)

        out["measurement_value"] = new_vals
        out["measurement_unit"] = new_units

    # --- replicate / expected as numeric ---
    for col in ("replicate", "concentration_expected"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col].replace({"": np.nan}), errors="coerce")

    # --- timestamps ---
    if "timestamp" in out.columns:
        new_ts = []
        for _, row in out.iterrows():
            raw = row["timestamp"]
            well = _well_of(row)
            iso = _parse_date(raw)
            if iso is None:
                audit.flag("DATE_UNPARSEABLE", well, severity="warn",
                           detail=f"could not parse date '{raw}'", field_name="timestamp")
                new_ts.append(None)
            else:
                if iso != str(raw).strip():
                    audit.normalized("timestamp", well, raw, iso, "standardized to ISO 8601",
                                     rule="DATE_NORMALIZED")
                new_ts.append(iso)
        out["timestamp"] = new_ts

    return out


def _parse_date(raw: object) -> Optional[str]:
    s = str(raw).strip()
    if s == "" or s.lower() in MISSING_SENTINELS:
        return None
    try:
        dt = dparser.parse(s, fuzzy=False)
        return dt.isoformat()
    except (ValueError, OverflowError):
        return None
