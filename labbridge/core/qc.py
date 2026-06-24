"""Quality-control flagging on a normalized canonical frame.

Structural checks (well validity, duplicates, negatives) and statistical checks
(robust MAD outliers, replicate CV, plate edge effect, control sanity). Every
issue is appended to the shared :class:`AuditLog`; nothing is dropped silently.

Statistical choices are deliberate and defensible:
* outliers use median + MAD, not mean + std -- robust to the very outliers we
  are trying to find;
* replicate spread uses coefficient of variation within a sample/analyte group.

Plate edge-effect detection is intentionally deferred to Phase 4: with few,
heterogeneous samples per plate the outer-vs-interior comparison is not reliable,
and Phase 1 only ships checks that are defensible on this data.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .audit import AuditLog
from .schema import REQUIRED_FIELD_NAMES, CONTROL_TYPES

_WELL_RE = re.compile(r"^[A-H](1[0-2]|[1-9])$")

# tunable thresholds (kept here so they're easy to point at in a demo)
MAD_Z_THRESHOLD = 3.5
REPLICATE_CV_THRESHOLD = 0.20      # 20%
BLANK_MAX = 1.0                    # a blank reading above this is suspicious (nM)
NEGATIVE_CONTROL_MAX = 5.0
POSITIVE_CONTROL_MIN = 10.0


def run_qc(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Run all QC checks, writing flags to ``audit``. Returns df unchanged."""
    _check_required_fields(df, audit)
    _check_well_validity(df, audit)
    _check_duplicates(df, audit)
    _check_negatives(df, audit)
    _check_mad_outliers(df, audit)
    _check_replicate_cv(df, audit)
    _check_controls(df, audit)
    return df


def _check_required_fields(df: pd.DataFrame, audit: AuditLog) -> None:
    for f in REQUIRED_FIELD_NAMES:
        if f not in df.columns:
            audit.flag("REQUIRED_FIELD_MISSING", well=None, severity="error",
                       detail=f"required canonical field '{f}' is absent", field_name=f)


def _check_well_validity(df: pd.DataFrame, audit: AuditLog) -> None:
    if "well_position" not in df.columns:
        return
    for w in df["well_position"]:
        if not (isinstance(w, str) and _WELL_RE.match(w)):
            audit.flag("INVALID_WELL", well=str(w), severity="error",
                       detail=f"'{w}' is not a valid A1..H12 coordinate",
                       field_name="well_position")


def _check_duplicates(df: pd.DataFrame, audit: AuditLog) -> None:
    if "well_position" not in df.columns:
        return
    keys = ["plate_id", "well_position"] if "plate_id" in df.columns else ["well_position"]
    dup_mask = df.duplicated(subset=keys, keep=False)
    for w in df.loc[dup_mask, "well_position"].unique():
        audit.flag("DUPLICATE_WELL", well=str(w), severity="error",
                   detail="same well appears more than once", field_name="well_position")


def _check_negatives(df: pd.DataFrame, audit: AuditLog) -> None:
    if "measurement_value" not in df.columns:
        return
    for _, row in df.iterrows():
        v = row["measurement_value"]
        if pd.notna(v) and v < 0:
            audit.flag("NEGATIVE_VALUE", well=str(row.get("well_position")),
                       severity="error", detail=f"negative reading {v}",
                       field_name="measurement_value")


def _sample_mask(df: pd.DataFrame) -> pd.Series:
    if "control_type" in df.columns:
        return df["control_type"].astype(str).str.lower().eq("sample")
    return pd.Series(True, index=df.index)


def _check_mad_outliers(df: pd.DataFrame, audit: AuditLog) -> None:
    if "measurement_value" not in df.columns or "analyte" not in df.columns:
        return
    sample = df[_sample_mask(df)]
    for analyte, grp in sample.groupby("analyte"):
        vals = pd.to_numeric(grp["measurement_value"], errors="coerce").dropna()
        # negatives are separately flagged; excluding them keeps the robust
        # centre/scale from being distorted by an impossible reading.
        vals = vals[vals >= 0]
        if len(vals) < 5:
            continue
        med = vals.median()
        mad = (vals - med).abs().median()
        # Convert MAD to a std-equivalent scale (0.6745 = MAD/sigma for normals).
        # If MAD collapses to 0 (many identical readings) fall back to std so a
        # genuine outlier is not silently missed.
        scale = mad / 0.6745 if mad > 0 else vals.std(ddof=0)
        if scale == 0:
            continue
        z = (vals - med) / scale
        for idx, score in z.items():
            if abs(score) > MAD_Z_THRESHOLD:
                audit.flag("OUTLIER_MAD", well=str(df.loc[idx, "well_position"]),
                           severity="warn",
                           detail=f"robust z={score:.1f} for analyte '{analyte}'",
                           field_name="measurement_value")


def _check_replicate_cv(df: pd.DataFrame, audit: AuditLog) -> None:
    needed = {"sample_id", "analyte", "measurement_value"}
    if not needed.issubset(df.columns):
        return
    sample = df[_sample_mask(df)]
    for (sid, analyte), grp in sample.groupby(["sample_id", "analyte"]):
        vals = pd.to_numeric(grp["measurement_value"], errors="coerce").dropna()
        if len(vals) < 2 or vals.mean() == 0:
            continue
        cv = vals.std(ddof=0) / abs(vals.mean())
        if cv > REPLICATE_CV_THRESHOLD:
            wells = ", ".join(str(w) for w in grp["well_position"])
            for w in grp["well_position"]:
                audit.flag("REPLICATE_CV_HIGH", well=str(w), severity="warn",
                           detail=f"sample {sid}: CV={cv:.0%} across wells [{wells}]",
                           field_name="measurement_value")


def _check_controls(df: pd.DataFrame, audit: AuditLog) -> None:
    if not {"control_type", "measurement_value"}.issubset(df.columns):
        return
    for _, row in df.iterrows():
        ctype = str(row.get("control_type")).lower()
        v = row.get("measurement_value")
        well = str(row.get("well_position"))
        if pd.isna(v):
            continue
        if ctype == "blank" and v > BLANK_MAX:
            audit.flag("CONTROL_FAIL", well=well, severity="error",
                       detail=f"blank reads {v} (> {BLANK_MAX})", field_name="control_type")
        elif ctype == "negative" and v > NEGATIVE_CONTROL_MAX:
            audit.flag("CONTROL_FAIL", well=well, severity="error",
                       detail=f"negative control reads {v} (> {NEGATIVE_CONTROL_MAX})",
                       field_name="control_type")
        elif ctype == "positive" and v < POSITIVE_CONTROL_MIN:
            audit.flag("CONTROL_FAIL", well=well, severity="error",
                       detail=f"positive control reads {v} (< {POSITIVE_CONTROL_MIN})",
                       field_name="control_type")
