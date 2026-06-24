"""Tests for the QC-intelligence layer.

Regenerates the deterministic sample plate + control history, runs the existing
pipeline to get a clean frame, then asserts the scientific checks behave.
"""

from pathlib import Path

import pytest

from labbridge.data import generate_samples, generate_control_runs
from labbridge.pipeline import run_pipeline
from labbridge.core import qc_intel
import pandas as pd

DATA = Path(generate_samples.__file__).resolve().parent
MESSY = DATA / "messy_plate_long.csv"
CONTROLS = DATA / "control_runs.csv"


@pytest.fixture(scope="module", autouse=True)
def _generate():
    generate_samples.main()
    generate_control_runs.main()


@pytest.fixture(scope="module")
def clean():
    return run_pipeline(MESSY).clean


def test_standard_curve_fits_and_passes(clean):
    curve = qc_intel.fit_standard_curve(clean)
    assert curve is not None
    assert curve.n >= 8                      # 8 standard levels (in duplicate)
    assert curve.r2 >= qc_intel.CURVE_R2_MIN
    assert curve.passed is True


def test_backcalculation_has_confidence_intervals(clean):
    curve = qc_intel.fit_standard_curve(clean)
    bc = qc_intel.backcalculate(clean, curve)
    assert not bc.empty
    for col in ("calc_conc_nM", "ci95_low", "ci95_high", "in_curve_range"):
        assert col in bc.columns
    assert (bc["ci95_low"] <= bc["calc_conc_nM"]).all()
    assert (bc["calc_conc_nM"] <= bc["ci95_high"]).all()


def test_replicate_reliability_flags_injected_break(clean):
    reps = qc_intel.replicate_reliability(clean)
    assert not reps.empty
    # S002 has a replicate (well B4) inflated x4 on purpose -> must fail CV
    s002 = reps[reps["sample_id"] == "S002"]
    assert not s002.empty
    assert s002.iloc[0]["reliability"] == "fail"


def test_anomaly_detection_is_rule_free_and_catches_outlier(clean):
    anom = qc_intel.detect_anomalies(clean)
    # the x4-inflated well B4 should surface as a robust outlier
    assert "B4" in set(anom["well_position"])


def test_control_chart_detects_drift():
    hist = pd.read_csv(CONTROLS)
    chart = qc_intel.control_chart(hist, "positive")
    assert chart is not None
    assert abs(chart.mean - 60.0) < 4         # baseline ~60 nM
    # the injected downward drift must register as a rejection-level violation
    assert len(chart.violations) > 0
    kinds = {k for _, k in chart.violations}
    assert kinds & {"1_3s", "2_2s"}


def test_negative_control_is_stable():
    hist = pd.read_csv(CONTROLS)
    chart = qc_intel.control_chart(hist, "negative")
    assert chart is not None
    assert chart.violations == []             # negative control stays in control


def test_fitness_verdict_is_review_or_fail(clean):
    curve = qc_intel.fit_standard_curve(clean)
    reps = qc_intel.replicate_reliability(clean)
    anom = qc_intel.detect_anomalies(clean)
    chart = qc_intel.control_chart(pd.read_csv(CONTROLS), "positive")
    fit = qc_intel.assess_fitness(curve, reps, anom, chart)
    assert fit.verdict in ("REVIEW", "FAIL")
    # the curve passing should be among the stated reasons
    assert any("passed" in text for _, text in fit.reasons)
