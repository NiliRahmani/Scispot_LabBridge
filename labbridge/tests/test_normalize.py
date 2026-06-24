"""Tests for value normalization."""

import numpy as np
import pandas as pd

from labbridge.core import normalize
from labbridge.core.audit import AuditLog


def _run(df):
    audit = AuditLog()
    out = normalize.normalize_frame(df, audit)
    return out, audit


def test_micromolar_converts_to_nanomolar():
    df = pd.DataFrame({
        "well_position": ["A1"], "measurement_value": ["0.012"],
        "measurement_unit": ["uM"], "control_type": ["sample"],
    })
    out, audit = _run(df)
    assert out["measurement_value"].iloc[0] == 12.0  # 0.012 uM -> 12 nM
    assert any(e.rule == "UNIT_NORMALIZED" for e in audit.events)


def test_decimal_comma_normalized():
    df = pd.DataFrame({"well_position": ["A1"], "measurement_value": ["1,23"],
                       "measurement_unit": ["nM"]})
    out, audit = _run(df)
    assert out["measurement_value"].iloc[0] == 1.23
    assert any(e.rule == "DECIMAL_NORMALIZED" for e in audit.events)


def test_embedded_unit_stripped():
    df = pd.DataFrame({"well_position": ["A1"], "measurement_value": ["12.3 nM"]})
    out, audit = _run(df)
    assert out["measurement_value"].iloc[0] == 12.3
    assert any(e.rule == "EMBEDDED_UNIT_STRIPPED" for e in audit.events)


def test_missing_and_overflow_become_nan_and_flag():
    df = pd.DataFrame({
        "well_position": ["A1", "B2", "C3"],
        "measurement_value": ["N/A", "OVRFLW", "5.0"],
        "measurement_unit": ["nM", "nM", "nM"],
    })
    out, audit = _run(df)
    assert np.isnan(out["measurement_value"].iloc[0])
    assert np.isnan(out["measurement_value"].iloc[1])
    assert out["measurement_value"].iloc[2] == 5.0
    rules = {e.rule for e in audit.flags()}
    assert "MISSING_VALUE" in rules and "SATURATED_OR_OVERFLOW" in rules


def test_well_and_control_standardized():
    df = pd.DataFrame({"well_position": [" a01 "], "control_type": ["Pos"],
                       "measurement_value": ["1.0"]})
    out, _ = _run(df)
    assert out["well_position"].iloc[0] == "A1"
    assert out["control_type"].iloc[0] == "positive"


def test_date_standardized_to_iso():
    df = pd.DataFrame({"well_position": ["A1"], "measurement_value": ["1.0"],
                       "timestamp": ["06/20/2026 9:14 AM"]})
    out, audit = _run(df)
    assert out["timestamp"].iloc[0].startswith("2026-06-20T09:14")
    assert any(e.rule == "DATE_NORMALIZED" for e in audit.events)
