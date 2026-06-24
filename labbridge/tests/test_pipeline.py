"""End-to-end pipeline test against the generated sample plate.

Regenerates the deterministic sample data, runs the full pipeline, and asserts
that the injected corruptions are caught and the recoverable ones normalized.
"""

import json
from pathlib import Path

import pytest

from labbridge.data import generate_samples
from labbridge.core import parsers
from labbridge.pipeline import run_pipeline

DATA = Path(generate_samples.__file__).resolve().parent
MESSY = DATA / "messy_plate_long.csv"


@pytest.fixture(scope="module", autouse=True)
def _generate():
    generate_samples.main()


def test_layout_detected_as_long():
    _, layout = parsers.load_table(MESSY)
    assert layout == "long"


def test_all_columns_map_to_canonical():
    result = run_pipeline(MESSY)
    unmapped = [m.source_column for m in result.mappings if m.bucket == "unmapped"]
    assert unmapped == []
    assert {"well_position", "measurement_value"}.issubset(result.clean.columns)


def test_injected_value_issues_are_flagged():
    result = run_pipeline(MESSY)
    by_well = {}
    for e in result.audit.flags():
        by_well.setdefault(e.well, set()).add(e.rule)

    # missing / overflow / negative / duplicate / control-fail land on the right wells
    assert "MISSING_VALUE" in by_well.get("D5", set())
    assert "MISSING_VALUE" in by_well.get("F9", set())
    assert "SATURATED_OR_OVERFLOW" in by_well.get("B7", set())
    assert "NEGATIVE_VALUE" in by_well.get("G6", set())
    assert "DUPLICATE_WELL" in by_well.get("A6", set())
    assert "CONTROL_FAIL" in by_well.get("E3", set())


def test_injected_replicate_break_is_flagged():
    result = run_pipeline(MESSY)
    cv_wells = {e.well for e in result.audit.flags() if e.rule == "REPLICATE_CV_HIGH"}
    assert "B4" in cv_wells  # the x4 replicate we broke on purpose


def test_recoverable_values_were_normalized():
    result = run_pipeline(MESSY)
    rules = {e.rule for e in result.audit.events if e.action == "normalize"}
    assert "UNIT_NORMALIZED" in rules        # uM -> nM (E8/E9)
    assert "DECIMAL_NORMALIZED" in rules      # 1,23 -> 1.23 (C10/D10)
    assert "EMBEDDED_UNIT_STRIPPED" in rules  # "x nM" (H4/H5)
    assert "DATE_NORMALIZED" in rules


def test_summary_is_not_ml_ready_due_to_errors():
    result = run_pipeline(MESSY)
    # error-severity flags (overflow, negative, duplicate, control fail) exist
    assert result.summary["flags_by_severity"].get("error", 0) > 0
    assert result.summary["ml_ready"] is False


def test_qc_flag_column_present_and_populated():
    result = run_pipeline(MESSY)
    assert "qc_flag" in result.clean.columns
    assert (result.clean["qc_flag"].str.len() > 0).any()
