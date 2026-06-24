"""Canonical schema definition and column-name synonyms.

This module is the single source of truth for what a "clean" plate-reader
record looks like. The parser, mapper, normalizer and QC layers all reference
the canonical field names defined here so the pipeline stays consistent.

Deliberately framework-free: no Streamlit, no I/O. Pure data definitions so the
rest of ``core`` (and the tests) can import it cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FieldKind(str, Enum):
    """Coarse value type, used by content-inference and validation."""

    STRING = "string"
    FLOAT = "float"
    INT = "int"
    ENUM = "enum"
    DATETIME = "datetime"
    WELL = "well"  # e.g. A1..H12


@dataclass(frozen=True)
class CanonicalField:
    name: str
    kind: FieldKind
    required: bool = False
    # Accepted source header spellings (lowercased, punctuation-stripped).
    synonyms: tuple[str, ...] = field(default_factory=tuple)
    # For ENUM fields: the allowed canonical values.
    enum_values: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


# --- Canonical measurement units ------------------------------------------

# Concentration is normalized to nanomolar (nM). Optical density (OD) and
# relative fluorescence units (RFU) are unitless-ish readouts kept as-is.
CANONICAL_CONCENTRATION_UNIT = "nM"
OPTICAL_UNITS = ("OD", "RFU")

# Multiplicative factors to convert a source concentration unit -> nM.
CONCENTRATION_TO_NM = {
    "nm": 1.0,
    "nmol/l": 1.0,
    "um": 1_000.0,
    "µm": 1_000.0,
    "μm": 1_000.0,  # note: distinct unicode mu from the line above
    "umol/l": 1_000.0,
    "mm": 1_000_000.0,
    "mmol/l": 1_000_000.0,
    "m": 1_000_000_000.0,
    "mol/l": 1_000_000_000.0,
    "pm": 0.001,
    "pmol/l": 0.001,
}

# Tokens that mean "no value" when they appear in a value cell.
MISSING_SENTINELS = {"", "n/a", "na", "nan", "nd", "n.d.", "-", "--", "none", "null"}

# Tokens that indicate a detector saturated / overflowed.
OVERFLOW_TOKENS = {"ovrflw", "over", "overflow", "sat", "saturated", "oor", "####"}


# --- Control / well-type vocabulary ----------------------------------------

CONTROL_TYPES = ("sample", "positive", "negative", "blank", "standard")

# Source spellings -> canonical control type.
CONTROL_SYNONYMS = {
    "sample": "sample",
    "unknown": "sample",
    "test": "sample",
    "pos": "positive",
    "positive": "positive",
    "pos ctrl": "positive",
    "positive control": "positive",
    "pc": "positive",
    "neg": "negative",
    "negative": "negative",
    "neg ctrl": "negative",
    "negative control": "negative",
    "nc": "negative",
    "blank": "blank",
    "blk": "blank",
    "buffer": "blank",
    "std": "standard",
    "standard": "standard",
    "cal": "standard",
    "calibrator": "standard",
}


# --- The canonical schema ---------------------------------------------------

CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField(
        "plate_id", FieldKind.STRING,
        synonyms=("plate", "plateid", "plate id", "plate barcode", "barcode"),
        description="Plate identifier.",
    ),
    CanonicalField(
        "well_position", FieldKind.WELL, required=True,
        synonyms=("well", "wellid", "well id", "well position", "position", "pos", "location"),
        description="Well coordinate A1..H12.",
    ),
    CanonicalField(
        "sample_id", FieldKind.STRING,
        synonyms=("sample", "sampleid", "sample id", "sample name", "specimen", "specimen id"),
        description="Sample/specimen identifier.",
    ),
    CanonicalField(
        "control_type", FieldKind.ENUM, enum_values=CONTROL_TYPES,
        synonyms=("control", "control type", "type", "welltype", "well type", "role"),
        description="Well role: sample/positive/negative/blank/standard.",
    ),
    CanonicalField(
        "analyte", FieldKind.STRING,
        synonyms=("analyte", "target", "marker", "assay", "assay name", "channel"),
        description="Measured analyte / target.",
    ),
    CanonicalField(
        "measurement_value", FieldKind.FLOAT, required=True,
        synonyms=("value", "result", "reading", "read", "signal", "measurement",
                  "conc", "concentration", "rfu", "od", "od450", "od600", "absorbance",
                  "fluorescence", "raw"),
        description="Numeric readout (normalized).",
    ),
    CanonicalField(
        "measurement_unit", FieldKind.STRING,
        synonyms=("unit", "units", "uom"),
        description="Canonical unit (nM, OD, RFU).",
    ),
    CanonicalField(
        "replicate", FieldKind.INT,
        synonyms=("replicate", "rep", "repl", "replicate number", "rep no"),
        description="Replicate index within a sample.",
    ),
    CanonicalField(
        "concentration_expected", FieldKind.FLOAT,
        synonyms=("expected", "expected conc", "nominal", "nominal conc",
                  "standard conc", "known conc"),
        description="Nominal concentration for standards (nM).",
    ),
    CanonicalField(
        "timestamp", FieldKind.DATETIME,
        synonyms=("timestamp", "date", "time", "datetime", "date time",
                  "read date", "read time", "read date/time", "acquired"),
        description="Read timestamp (ISO 8601).",
    ),
    CanonicalField(
        "operator", FieldKind.STRING,
        synonyms=("operator", "user", "analyst", "performed by", "scientist", "initials"),
        description="Operator who ran the read.",
    ),
    CanonicalField(
        "instrument_id", FieldKind.STRING,
        synonyms=("instrument", "instrumentid", "instrument id", "reader",
                  "device", "serial", "serial no"),
        description="Instrument identifier.",
    ),
)

CANONICAL_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in CANONICAL_FIELDS)
REQUIRED_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in CANONICAL_FIELDS if f.required)

_FIELD_BY_NAME = {f.name: f for f in CANONICAL_FIELDS}


def get_field(name: str) -> Optional[CanonicalField]:
    return _FIELD_BY_NAME.get(name)


def canonical_control_type(raw: object) -> Optional[str]:
    """Map a raw control/type token to a canonical control type, else None."""
    if raw is None:
        return None
    key = str(raw).strip().lower()
    return CONTROL_SYNONYMS.get(key)
