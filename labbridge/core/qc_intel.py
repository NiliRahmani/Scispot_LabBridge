"""QC-intelligence layer — the scientific quality checks that sit *on top of*
clean data, which rule-based ETL flagging does not provide:

* standard-curve fitting + acceptance (R²) and back-calculation with 95% CIs,
* replicate reliability (per-sample CV classification),
* automatic statistical anomaly detection (robust median/MAD — no hand-written
  rules),
* Levey-Jennings control monitoring across runs with Westgard-style violations,
* an overall fitness-for-analysis verdict.

Deterministic, numpy-only, framework-free so the UI and tests can call it cheaply.
This module intentionally does not modify the existing ingest/normalize/QC core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# --- acceptance thresholds (explainable, referenced by UI) -----------------
CURVE_R2_MIN = 0.98
REPLICATE_CV_GOOD = 15.0      # %CV
REPLICATE_CV_FAIL = 25.0      # %CV
ANOMALY_Z = 3.5               # robust z (median/MAD)
WESTGARD_SHIFT = 8            # consecutive points on one side of mean
DEFAULT_BASELINE = 16         # runs used to establish control limits


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


# --- standard curve --------------------------------------------------------
@dataclass
class CurveFit:
    slope: float
    intercept: float
    r2: float
    residual_se: float
    n: int
    y_min: float
    y_max: float
    passed: bool
    note: str


def fit_standard_curve(clean: pd.DataFrame) -> Optional[CurveFit]:
    """Linear fit of measured signal vs. nominal concentration over the standards."""
    df = clean.copy()
    df["measurement_value"] = _num(df["measurement_value"])
    df["concentration_expected"] = _num(df.get("concentration_expected"))
    s = df[(df.get("control_type") == "standard")
           & df["measurement_value"].notna()
           & df["concentration_expected"].notna()]
    if len(s) < 3:
        return None
    x = s["concentration_expected"].to_numpy(float)
    y = s["measurement_value"].to_numpy(float)
    slope, intercept = (float(v) for v in np.polyfit(x, y, 1))
    yhat = slope * x + intercept
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    rse = float(np.sqrt(ss_res / max(len(x) - 2, 1)))
    passed = r2 >= CURVE_R2_MIN
    note = (f"Linear fit passed acceptance (R² ≥ {CURVE_R2_MIN:.2f})." if passed
            else f"Curve below acceptance (R² < {CURVE_R2_MIN:.2f}) — requantify before use.")
    return CurveFit(slope, intercept, r2, rse, len(x),
                    float(y.min()), float(y.max()), passed, note)


def backcalculate(clean: pd.DataFrame, curve: Optional[CurveFit]) -> pd.DataFrame:
    """Quantify samples from the curve, with an approximate 95% CI and a
    flag when the signal falls outside the validated curve range."""
    if curve is None or curve.slope == 0:
        return pd.DataFrame()
    df = clean.copy()
    df["measurement_value"] = _num(df["measurement_value"])
    s = df[(df.get("control_type") == "sample") & df["measurement_value"].notna()].copy()
    if s.empty:
        return pd.DataFrame()
    y = s["measurement_value"].to_numpy(float)
    x_hat = (y - curve.intercept) / curve.slope
    ci = 1.96 * curve.residual_se / abs(curve.slope)
    s["calc_conc_nM"] = np.round(x_hat, 3)
    s["ci95_low"] = np.round(x_hat - ci, 3)
    s["ci95_high"] = np.round(x_hat + ci, 3)
    s["in_curve_range"] = (y >= curve.y_min) & (y <= curve.y_max)
    cols = ["well_position", "sample_id", "replicate", "measurement_value",
            "calc_conc_nM", "ci95_low", "ci95_high", "in_curve_range"]
    return s[[c for c in cols if c in s.columns]].reset_index(drop=True)


# --- replicate reliability -------------------------------------------------
def replicate_reliability(clean: pd.DataFrame) -> pd.DataFrame:
    df = clean.copy()
    df["measurement_value"] = _num(df["measurement_value"])
    s = df[(df.get("control_type") == "sample") & df["measurement_value"].notna()]
    out = []
    for sid, g in s.groupby("sample_id"):
        vals = g["measurement_value"].to_numpy(float)
        if len(vals) < 2:
            continue
        mean = float(vals.mean())
        sd = float(vals.std(ddof=1))
        cv = sd / mean * 100 if mean else float("nan")
        cls = ("good" if cv <= REPLICATE_CV_GOOD
               else "watch" if cv <= REPLICATE_CV_FAIL else "fail")
        out.append({"sample_id": sid, "n_reps": len(vals), "mean": round(mean, 3),
                    "cv_pct": round(cv, 1), "reliability": cls})
    if not out:
        return pd.DataFrame(columns=["sample_id", "n_reps", "mean", "cv_pct", "reliability"])
    return pd.DataFrame(out).sort_values("cv_pct", ascending=False).reset_index(drop=True)


# --- automatic anomaly detection (robust, rule-free) -----------------------
def detect_anomalies(clean: pd.DataFrame) -> pd.DataFrame:
    df = clean.copy()
    df["measurement_value"] = _num(df["measurement_value"])
    s = df[(df.get("control_type") == "sample") & df["measurement_value"].notna()].copy()
    out = []
    for analyte, g in s.groupby("analyte"):
        v = g["measurement_value"].to_numpy(float)
        med = float(np.median(v))
        mad = float(np.median(np.abs(v - med)))
        scale = 1.4826 * mad if mad > 0 else float(v.std(ddof=1) or 0.0)
        if scale == 0:
            continue
        for (_, row), val in zip(g.iterrows(), v):
            z = (val - med) / scale
            if abs(z) > ANOMALY_Z:
                out.append({"well_position": row.get("well_position"),
                            "sample_id": row.get("sample_id"), "analyte": analyte,
                            "value": round(float(val), 3), "robust_z": round(float(z), 2)})
    if not out:
        return pd.DataFrame(columns=["well_position", "sample_id", "analyte", "value", "robust_z"])
    return pd.DataFrame(out).sort_values("robust_z", key=lambda c: c.abs(),
                                         ascending=False).reset_index(drop=True)


# --- Levey-Jennings control monitoring -------------------------------------
@dataclass
class ControlChart:
    control_type: str
    mean: float
    sd: float
    points: pd.DataFrame                 # run_id, run_date, value, z, flags
    violations: list = field(default_factory=list)   # rejection rules: (run_id, rule)
    trends: list = field(default_factory=list)       # warning trends: (run_id, rule)


def control_chart(history: pd.DataFrame, control_type: str = "positive",
                  baseline_n: int = DEFAULT_BASELINE) -> Optional[ControlChart]:
    h = history[history["control_type"] == control_type].copy().reset_index(drop=True)
    if h.empty:
        return None
    h["value"] = _num(h["value"])
    base = h["value"].iloc[:baseline_n]
    mean = float(base.mean())
    sd = float(base.std(ddof=1)) or 1.0
    z = ((h["value"] - mean) / sd).to_numpy(float)
    h["z"] = np.round(z, 2)

    flags = [[] for _ in range(len(h))]
    violations = []   # rejection-level (1_3s, 2_2s)
    trends = []       # warning-level (sustained shift)

    def add(i, rule, bucket):
        flags[i].append(rule)
        bucket.append((h["run_id"].iloc[i], rule))

    for i in range(len(z)):
        if abs(z[i]) > 3:                                  # 1_3s — hard reject
            add(i, "1_3s", violations)
        if i >= 1 and abs(z[i]) > 2 and abs(z[i - 1]) > 2 \
                and np.sign(z[i]) == np.sign(z[i - 1]):    # 2_2s — hard reject
            add(i, "2_2s", violations)

    run = 1                                                # shift: N on one side — trend
    for i in range(1, len(z)):
        same = np.sign(z[i]) == np.sign(z[i - 1]) and z[i] != 0
        run = run + 1 if same else 1
        if run >= WESTGARD_SHIFT:
            add(i, f"shift_{WESTGARD_SHIFT}x", trends)

    h["flags"] = [";".join(f) for f in flags]
    return ControlChart(control_type, round(mean, 3), round(sd, 3), h, violations, trends)


# --- overall fitness verdict ----------------------------------------------
@dataclass
class Fitness:
    verdict: str                 # "PASS" | "REVIEW" | "FAIL"
    reasons: list                # list of (severity, text)


def assess_fitness(curve: Optional[CurveFit], reps: pd.DataFrame,
                   anomalies: pd.DataFrame, chart: Optional[ControlChart]) -> Fitness:
    reasons: list = []
    level = 0  # 0 pass, 1 review, 2 fail

    if curve is None:
        reasons.append(("warn", "No standard curve found to validate quantification."))
        level = max(level, 1)
    elif not curve.passed:
        reasons.append(("error", f"Standard curve failed acceptance (R²={curve.r2:.3f})."))
        level = 2
    else:
        reasons.append(("ok", f"Standard curve passed (R²={curve.r2:.3f})."))

    n_fail = int((reps["reliability"] == "fail").sum()) if len(reps) else 0
    if n_fail:
        reasons.append(("error", f"{n_fail} sample(s) failed replicate reliability "
                                  f"(CV > {REPLICATE_CV_FAIL:.0f}%)."))
        level = 2

    if len(anomalies):
        reasons.append(("warn", f"{len(anomalies)} statistical outlier(s) flagged for review."))
        level = max(level, 1)

    if chart and chart.violations:
        kinds = ", ".join(sorted({k for _, k in chart.violations}))
        reasons.append(("error", f"Control monitoring detected drift / violations ({kinds})."))
        level = 2

    return Fitness(["PASS", "REVIEW", "FAIL"][level], reasons)
