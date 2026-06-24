"""Tests for QC flagging."""

import numpy as np
import pandas as pd

from labbridge.core import qc
from labbridge.core.audit import AuditLog


def _flags(df):
    audit = AuditLog()
    qc.run_qc(df, audit)
    return {e.rule for e in audit.flags()}, audit


def test_duplicate_well_flagged():
    df = pd.DataFrame({
        "plate_id": ["P1", "P1"], "well_position": ["A1", "A1"],
        "control_type": ["sample", "sample"], "analyte": ["x", "x"],
        "measurement_value": [1.0, 1.0], "sample_id": ["S1", "S1"],
    })
    rules, _ = _flags(df)
    assert "DUPLICATE_WELL" in rules


def test_negative_value_flagged():
    df = pd.DataFrame({"well_position": ["A1"], "control_type": ["sample"],
                       "analyte": ["x"], "measurement_value": [-2.0],
                       "sample_id": ["S1"]})
    rules, _ = _flags(df)
    assert "NEGATIVE_VALUE" in rules


def test_invalid_well_flagged():
    df = pd.DataFrame({"well_position": ["Z99"], "control_type": ["sample"],
                       "analyte": ["x"], "measurement_value": [1.0],
                       "sample_id": ["S1"]})
    rules, _ = _flags(df)
    assert "INVALID_WELL" in rules


def test_mad_outlier_flagged():
    # realistic spread (instrument noise) plus one gross outlier in the last well
    vals = [19.5, 20.1, 20.3, 19.8, 20.0, 20.2, 19.7, 20.4, 19.9, 20.1, 200.0]
    df = pd.DataFrame({
        "well_position": [f"A{i+1}" for i in range(11)],
        "control_type": ["sample"] * 11, "analyte": ["x"] * 11,
        "sample_id": [f"S{i}" for i in range(11)], "measurement_value": vals,
    })
    rules, audit = _flags(df)
    assert "OUTLIER_MAD" in rules
    outlier_wells = {e.well for e in audit.flags() if e.rule == "OUTLIER_MAD"}
    assert "A11" in outlier_wells


def test_replicate_cv_flagged():
    df = pd.DataFrame({
        "well_position": ["A1", "A2", "A3"], "control_type": ["sample"] * 3,
        "analyte": ["x"] * 3, "sample_id": ["S1"] * 3,
        "measurement_value": [10.0, 10.0, 40.0],  # one rep way off
    })
    rules, _ = _flags(df)
    assert "REPLICATE_CV_HIGH" in rules


def test_blank_control_fail():
    df = pd.DataFrame({"well_position": ["A1"], "control_type": ["blank"],
                       "analyte": ["x"], "measurement_value": [55.0],
                       "sample_id": ["BLANK"]})
    rules, _ = _flags(df)
    assert "CONTROL_FAIL" in rules


def test_clean_sample_has_no_flags():
    df = pd.DataFrame({
        "well_position": ["A1", "A2", "A3"], "control_type": ["sample"] * 3,
        "analyte": ["x"] * 3, "sample_id": ["S1"] * 3,
        "measurement_value": [20.0, 20.5, 19.8], "plate_id": ["P1"] * 3,
    })
    rules, _ = _flags(df)
    assert rules == set()
