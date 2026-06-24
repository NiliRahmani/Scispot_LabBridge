"""LabBridge — Streamlit demo UI (Phase 3).

A 5-step wizard wrapped around the existing deterministic pipeline in
``labbridge.core`` / ``labbridge.pipeline``. This module is UI only: it loads,
maps, normalizes, QCs and exports by calling the pipeline — it does not contain
any data logic of its own.

Run:
    streamlit run labbridge/app.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Ensure the repository root (the parent of the ``labbridge`` package) is on
# ``sys.path``. ``streamlit run labbridge/app.py`` puts only the script's own
# directory on the path, so ``import labbridge.core`` would otherwise fail on
# Streamlit Community Cloud while still working from the repo root locally.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from labbridge.core import parsers, mapping, qc_intel
from labbridge.core.audit import summary_to_markdown
from labbridge.core.schema import CANONICAL_FIELD_NAMES, get_field
from labbridge.pipeline import run_pipeline

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "data" / "messy_plate_long.csv"
CONTROLS = HERE / "data" / "control_runs.csv"

STEPS = [
    "1 · Upload",
    "2 · Schema mapping",
    "3 · Data QC",
    "4 · QC Intelligence",
    "5 · Clean preview",
    "6 · Export",
]
UNMAPPED = "— leave unmapped —"
BUCKET_BADGE = {
    "auto": ("🟢", "#1a7f37", "auto-mapped"),
    "review": ("🟡", "#9a6700", "needs review"),
    "unmapped": ("🔴", "#cf222e", "unmapped"),
}
SEV_BADGE = {"error": "🔴 error", "warn": "🟠 warn", "info": "🔵 info"}


# --------------------------------------------------------------------------- #
# session state
# --------------------------------------------------------------------------- #
def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("step", 0)
    ss.setdefault("source_path", None)
    ss.setdefault("source_name", None)
    ss.setdefault("raw", None)
    ss.setdefault("layout", None)
    ss.setdefault("mappings", None)
    ss.setdefault("result", None)


def _reset() -> None:
    for k in ("step", "source_path", "source_name", "raw", "layout",
              "mappings", "result"):
        st.session_state.pop(k, None)
    _init_state()


def _load_source(path: Path, name: str) -> None:
    """Ingest a file and propose mappings; stash everything in session."""
    raw, layout = parsers.load_table(path)
    st.session_state.update(
        source_path=str(path),
        source_name=name,
        raw=raw,
        layout=layout,
        mappings=mapping.propose_mappings(raw),
        result=None,
    )


# --------------------------------------------------------------------------- #
# step renderers
# --------------------------------------------------------------------------- #
def step_upload() -> None:
    st.subheader("Upload a messy instrument export")
    st.caption(
        "Drop a plate-reader / assay export (CSV or Excel), or load the bundled "
        "sample. LabBridge detects the file structure before anything else."
    )

    col_a, col_b = st.columns([3, 2], vertical_alignment="center")
    with col_a:
        uploaded = st.file_uploader(
            "Instrument export (CSV or Excel)", type=["csv", "xlsx", "xls"],
        )
        if uploaded is not None:
            suffix = Path(uploaded.name).suffix or ".csv"
            tmp = Path(tempfile.gettempdir()) / f"labbridge_upload{suffix}"
            tmp.write_bytes(uploaded.getbuffer())
            _load_source(tmp, uploaded.name)
    with col_b:
        if st.button("📄 Load sample plate export", type="primary",
                     use_container_width=True):
            _load_source(SAMPLE, SAMPLE.name)
        st.caption("96-well plate · long-format export with realistic data issues")

    if st.session_state.raw is None:
        st.info(
            "**No file yet.** Click **Load sample plate export** to start, or drop "
            "your own export above. Nothing is uploaded anywhere — it stays in this "
            "session."
        )
        return

    layout = st.session_state.layout
    st.success(
        f"Loaded **{st.session_state.source_name}** · detected format: "
        f"**{layout}** · {len(st.session_state.raw)} rows × "
        f"{len(st.session_state.raw.columns)} columns"
    )
    st.markdown("**Raw preview** (exactly as the instrument exported it):")
    st.dataframe(st.session_state.raw, use_container_width=True, height=280)


def step_mapping() -> None:
    st.subheader("Review the proposed schema mapping")
    st.caption(
        "Each raw column is matched to the canonical schema with an explainable "
        "confidence score. Green is auto-mapped; yellow is proposed but asks you "
        "to confirm; red is left unmapped. You stay in control of every column."
    )
    mappings = st.session_state.mappings
    options = [UNMAPPED] + list(CANONICAL_FIELD_NAMES)

    # header row
    h = st.columns([2.2, 3.0, 1.4, 2.4])
    for c, label in zip(h, ["Raw column", "Proposed mapping", "Confidence", "Your decision"]):
        c.markdown(f"**{label}**")

    selections: dict[str, str] = {}
    for m in mappings:
        emoji, color, word = BUCKET_BADGE[m.bucket]
        row = st.columns([2.2, 3.0, 1.4, 2.4])
        row[0].markdown(f"`{m.source_column}`")
        target_txt = m.target_field if m.target_field else "—"
        row[1].markdown(
            f"{emoji} **{target_txt}** &nbsp;<span style='color:{color}'>"
            f"({word})</span><br><span style='font-size:0.8em;color:#57606a'>"
            f"{m.reason}</span>",
            unsafe_allow_html=True,
        )
        row[2].markdown(
            f"<div style='background:{color};color:white;border-radius:6px;"
            f"text-align:center;padding:2px 0;font-weight:600'>"
            f"{m.confidence:.0%}</div>",
            unsafe_allow_html=True,
        )
        default = m.target_field if m.target_field in CANONICAL_FIELD_NAMES else UNMAPPED
        selections[m.source_column] = row[3].selectbox(
            f"map_{m.source_column}", options,
            index=options.index(default),
            label_visibility="collapsed",
        )

    st.divider()
    st.markdown(
        "🟡 **One column needs your judgement.** `Anlyst` looks like **operator** "
        "(~78% — fuzzy match only), so LabBridge asks before trusting it. "
        "Confirm or correct it, then run the quality checks."
    )

    if st.button("✅ Confirm mapping & run quality checks", type="primary"):
        overrides: dict[str, str | None] = {}
        for m in mappings:
            chosen = selections[m.source_column]
            chosen_target = None if chosen == UNMAPPED else chosen
            # Record a human decision when the user changed the proposal, or when
            # they signed off on anything that was not already auto-mapped.
            if chosen_target != m.target_field or m.bucket != "auto":
                overrides[m.source_column] = chosen_target
        st.session_state.result = run_pipeline(
            st.session_state.source_path, overrides=overrides
        )
        _goto(2)
        st.rerun()


def _plate_frame(clean: pd.DataFrame) -> pd.DataFrame:
    """Long-form per-well frame for the Altair heatmap."""
    df = clean.copy()
    df["measurement_value"] = pd.to_numeric(df["measurement_value"], errors="coerce")
    df["row"] = df["well_position"].astype(str).str[0]
    df["col"] = df["well_position"].astype(str).str[1:]
    df["col"] = pd.to_numeric(df["col"], errors="coerce")
    df = df.dropna(subset=["col"])
    df["col"] = df["col"].astype(int)
    df["flagged"] = df.get("qc_flag", "").astype(str).str.len() > 0
    # one row per well (duplicates collapse to first for the grid)
    df = df.sort_values("flagged", ascending=False).drop_duplicates("well_position")
    keep = ["well_position", "row", "col", "measurement_value",
            "measurement_unit", "sample_id", "control_type", "qc_flag", "flagged"]
    return df[[c for c in keep if c in df.columns]]


def step_qc() -> None:
    result = st.session_state.result
    s = result.summary
    st.subheader("Quality control & anomaly review")

    m = st.columns(5)
    m[0].metric("Rows", f"{s['rows_in']} → {s['rows_out']}")
    m[1].metric("QC flags", s["total_flags"])
    m[2].metric("Errors", s["flags_by_severity"].get("error", 0))
    m[3].metric("Warnings", s["flags_by_severity"].get("warn", 0))
    m[4].metric("ML-ready", "Yes" if s["ml_ready"] else "Not yet")

    left, right = st.columns([1.15, 1.0])

    with left:
        st.markdown("**Plate map** — colour = reading (log), ▲ = flagged well")
        pf = _plate_frame(result.clean)
        # Log colour scale spreads the wide reading range (controls/standards vs
        # samples) so real differences are visible. Log needs strictly-positive
        # input, so non-positive / missing wells colour as null (they carry a ▲).
        pf = pf.assign(
            color_value=pf["measurement_value"].where(pf["measurement_value"] > 0)
        )
        base = alt.Chart(pf).encode(
            x=alt.X("col:O", title="column", sort=list(range(1, 13))),
            y=alt.Y("row:O", title="row", sort=list("ABCDEFGH")),
        )
        cells = base.mark_rect(stroke="white", strokeWidth=1).encode(
            color=alt.Color("color_value:Q",
                            scale=alt.Scale(type="log", scheme="viridis"),
                            legend=alt.Legend(title="reading (log)")),
            tooltip=["well_position", "sample_id", "control_type",
                     "measurement_value", "measurement_unit", "qc_flag"],
        )
        flags = base.transform_filter(alt.datum.flagged).mark_point(
            shape="triangle-up", size=90, color="#cf222e",
            stroke="white", strokeWidth=1, filled=True,
        )
        st.altair_chart((cells + flags).properties(height=320),
                        use_container_width=True)

    with right:
        st.markdown("**Flagged wells** — every flag says which well and why")
        flag_rows = [
            {"sev": SEV_BADGE.get(e.severity, e.severity),
             "well": e.well, "flag": e.rule, "reason": e.detail}
            for e in result.audit.flags()
        ]
        fdf = pd.DataFrame(flag_rows)
        if fdf.empty:
            st.success("No quality issues detected.")
        else:
            order = {"🔴 error": 0, "🟠 warn": 1, "🔵 info": 2}
            fdf = fdf.sort_values("sev", key=lambda c: c.map(order)).reset_index(drop=True)
            st.dataframe(
                fdf, use_container_width=True, height=320, hide_index=True,
                column_config={
                    "sev": st.column_config.TextColumn("severity"),
                    "well": st.column_config.TextColumn("well", width="small"),
                    "flag": st.column_config.TextColumn("flag"),
                    "reason": st.column_config.TextColumn("reason", width="large"),
                },
            )

    with st.expander("What these flags mean"):
        st.markdown(
            "- **MISSING_VALUE / SATURATED_OR_OVERFLOW / NEGATIVE_VALUE** — "
            "unusable readings, excluded from analysis.\n"
            "- **DUPLICATE_WELL** — the same well appears twice.\n"
            "- **CONTROL_FAIL** — a control well read outside its expected range.\n"
            "- **REPLICATE_CV_HIGH** — replicates of one sample disagree too much.\n"
            "- **OUTLIER_MAD** — a robust (median/MAD) statistical outlier — not a "
            "fragile mean-based check."
        )


VERDICT_STYLE = {
    "PASS": ("#1a7f37", "✅", "Fit for analysis"),
    "REVIEW": ("#9a6700", "⚠️", "Usable — review required"),
    "FAIL": ("#cf222e", "⛔", "Not fit for analysis yet"),
}
REASON_ICON = {"ok": "✅", "warn": "🟠", "error": "🔴"}


def step_qc_intel() -> None:
    result = st.session_state.result
    clean = result.clean
    st.subheader("QC Intelligence — scientific validation on top of clean data")
    st.caption(
        "Clean, ML-ready data still isn't *trusted* data. This layer adds the "
        "scientific checks — curve fit, replicate reliability, automatic anomaly "
        "detection, and control monitoring — that decide whether results can be used."
    )

    curve = qc_intel.fit_standard_curve(clean)
    reps = qc_intel.replicate_reliability(clean)
    anom = qc_intel.detect_anomalies(clean)
    history = pd.read_csv(CONTROLS) if CONTROLS.exists() else pd.DataFrame()
    chart = qc_intel.control_chart(history, "positive") if not history.empty else None
    fitness = qc_intel.assess_fitness(curve, reps, anom, chart)

    t_curve, t_rep, t_ctrl, t_verdict = st.tabs([
        "📈 Standard curve", "🔁 Replicate reliability",
        "📉 Control monitoring", "✅ Fitness verdict",
    ])

    # --- standard curve ----------------------------------------------------
    with t_curve:
        if curve is None:
            st.info("No standards found on this plate to build a curve.")
        else:
            m = st.columns(4)
            m[0].metric("R²", f"{curve.r2:.4f}")
            m[1].metric("Slope", f"{curve.slope:.3f}")
            m[2].metric("Std levels", curve.n)
            m[3].metric("Acceptance", "PASS" if curve.passed else "FAIL")
            std = clean.copy()
            std["measurement_value"] = pd.to_numeric(std["measurement_value"], errors="coerce")
            std["concentration_expected"] = pd.to_numeric(std["concentration_expected"], errors="coerce")
            std = std[(std["control_type"] == "standard")
                      & std["measurement_value"].notna()
                      & std["concentration_expected"].notna()]
            x_lo = float(std["concentration_expected"].min())
            x_hi = float(std["concentration_expected"].max())
            fit_line = pd.DataFrame({"concentration_expected": [x_lo, x_hi]})
            fit_line["measurement_value"] = curve.slope * fit_line["concentration_expected"] + curve.intercept
            pts = alt.Chart(std).mark_circle(size=90, color="#2563eb", opacity=0.8).encode(
                x=alt.X("concentration_expected:Q", title="expected concentration (nM)"),
                y=alt.Y("measurement_value:Q", title="measured signal"),
                tooltip=["well_position", "concentration_expected", "measurement_value"],
            )
            line = alt.Chart(fit_line).mark_line(color="#f59e0b", strokeWidth=2).encode(
                x="concentration_expected:Q", y="measurement_value:Q")
            st.altair_chart((pts + line).properties(height=300), use_container_width=True)
            st.caption(curve.note + "  Samples are quantified from this curve with 95% confidence intervals:")
            bc = qc_intel.backcalculate(clean, curve)
            st.dataframe(bc.head(8), use_container_width=True, hide_index=True)

    # --- replicate reliability --------------------------------------------
    with t_rep:
        if reps.empty:
            st.info("No replicated samples to evaluate.")
        else:
            n_fail = int((reps["reliability"] == "fail").sum())
            st.markdown(f"**{n_fail} sample(s) fail replicate agreement** "
                        f"(CV > {qc_intel.REPLICATE_CV_FAIL:.0f}%). "
                        "These would silently corrupt any model trained on them.")
            color = alt.Scale(domain=["good", "watch", "fail"],
                              range=["#1a7f37", "#9a6700", "#cf222e"])
            bar = alt.Chart(reps).mark_bar().encode(
                x=alt.X("cv_pct:Q", title="replicate CV (%)"),
                y=alt.Y("sample_id:N", sort="-x", title="sample"),
                color=alt.Color("reliability:N", scale=color, legend=alt.Legend(title="")),
                tooltip=["sample_id", "n_reps", "mean", "cv_pct", "reliability"],
            )
            rule = alt.Chart(pd.DataFrame({"x": [qc_intel.REPLICATE_CV_FAIL]})).mark_rule(
                color="#cf222e", strokeDash=[4, 4]).encode(x="x:Q")
            st.altair_chart((bar + rule).properties(height=320), use_container_width=True)

    # --- control monitoring (Levey-Jennings) ------------------------------
    with t_ctrl:
        if chart is None:
            st.info("No control-run history available.")
        else:
            pts = chart.points.copy().reset_index(drop=True)
            pts["run_no"] = range(1, len(pts) + 1)
            pts["violated"] = pts["flags"].str.contains("1_3s|2_2s", regex=True)
            st.markdown(
                f"**Positive control across {len(pts)} runs** — limits set from the "
                f"validated baseline (mean **{chart.mean} nM**, SD **{chart.sd}**). "
                f"**{len(chart.violations)} Westgard violation(s)** flag a reagent drift "
                "that single-run QC never sees.")
            bands = []
            for k, col, dash in [(2, "#9a6700", [4, 4]), (3, "#cf222e", [2, 2])]:
                for sign in (1, -1):
                    bands.append({"y": chart.mean + sign * k * chart.sd, "c": col, "d": str(dash)})
            band_df = pd.DataFrame(bands)
            mean_rule = alt.Chart(pd.DataFrame({"y": [chart.mean]})).mark_rule(
                color="#57606a").encode(y="y:Q")
            sd_rules = alt.Chart(band_df).mark_rule(strokeDash=[4, 4]).encode(
                y="y:Q", color=alt.Color("c:N", scale=None, legend=None))
            line = alt.Chart(pts).mark_line(color="#2563eb", point=False).encode(
                x=alt.X("run_no:Q", title="run #"),
                y=alt.Y("value:Q", title="positive control (nM)",
                        scale=alt.Scale(zero=False)))
            good = alt.Chart(pts[~pts["violated"]]).mark_point(
                color="#2563eb", filled=True, size=55).encode(x="run_no:Q", y="value:Q",
                tooltip=["run_id", "value", "z", "flags"])
            bad = alt.Chart(pts[pts["violated"]]).mark_point(
                color="#cf222e", filled=True, size=120, shape="triangle-up").encode(
                x="run_no:Q", y="value:Q", tooltip=["run_id", "value", "z", "flags"])
            st.altair_chart((sd_rules + mean_rule + line + good + bad).properties(height=340),
                            use_container_width=True)

    # --- fitness verdict ---------------------------------------------------
    with t_verdict:
        color, icon, label = VERDICT_STYLE[fitness.verdict]
        st.markdown(
            f"<div style='background:{color};color:white;border-radius:12px;"
            f"padding:18px 24px;font-size:1.4em;font-weight:700'>"
            f"{icon} {fitness.verdict} — {label}</div>",
            unsafe_allow_html=True)
        st.write("")
        for sev, text in fitness.reasons:
            st.markdown(f"{REASON_ICON.get(sev, '•')} {text}")
        st.caption(
            "This is the layer GLUE-style ingestion stops short of: clean data is "
            "necessary, but only validated data is safe to model on.")


def step_clean() -> None:
    result = st.session_state.result
    st.subheader("Clean, standardized records")
    st.caption(
        "Every change is shown explicitly — nothing happens silently. "
        "Below: the values LabBridge normalized, then the full clean table."
    )

    changes = [
        {"well": e.well, "field": e.field_name, "rule": e.rule,
         "before": e.before, "after": e.after}
        for e in result.audit.events if e.action == "normalize"
    ]
    cdf = pd.DataFrame(changes)
    st.markdown(f"**Before → after** · {len(cdf)} values normalized")
    if cdf.empty:
        st.info("No normalizations were necessary.")
    else:
        show_all = st.toggle("Show all normalizations", value=False)
        view = cdf if show_all else cdf.head(12)
        st.dataframe(
            view, use_container_width=True, hide_index=True,
            column_config={
                "well": st.column_config.TextColumn("well", width="small"),
                "field": st.column_config.TextColumn("field"),
                "rule": st.column_config.TextColumn("transformation"),
                "before": st.column_config.TextColumn("before"),
                "after": st.column_config.TextColumn("after"),
            },
        )
        if not show_all and len(cdf) > 12:
            st.caption(f"… and {len(cdf) - 12} more (toggle above to see all).")

    st.markdown("**Clean dataset** (canonical schema + per-row `qc_flag`)")
    st.dataframe(result.clean, use_container_width=True, height=320)


def step_export() -> None:
    result = st.session_state.result
    s = result.summary
    st.subheader("Export & data-quality report")

    md = summary_to_markdown(s)
    clean_csv = result.clean.to_csv(index=False).encode("utf-8")
    audit_json = result.audit.to_json().encode("utf-8")

    c = st.columns(3)
    c[0].download_button("⬇️ Clean dataset (CSV)", clean_csv,
                         file_name="clean_dataset.csv", mime="text/csv",
                         use_container_width=True)
    c[1].download_button("⬇️ Quality summary (Markdown)", md.encode("utf-8"),
                         file_name="data_quality_summary.md", mime="text/markdown",
                         use_container_width=True)
    c[2].download_button("⬇️ Transformation log (JSON)", audit_json,
                         file_name="transformation_log.json",
                         mime="application/json", use_container_width=True)

    st.divider()
    left, right = st.columns([1.1, 1.0])
    with left:
        st.markdown("**Data-quality summary**")
        st.markdown(md)
    with right:
        st.markdown("**Transformation log** (audit trail, first rows)")
        log_df = pd.DataFrame(result.audit.to_records())
        st.dataframe(log_df, use_container_width=True, height=360, hide_index=True)

    if s["ml_ready"]:
        st.success("This dataset is standardized and ML-ready.")
    else:
        st.warning(
            "Standardized, with unusable readings clearly flagged and excluded — "
            "ready for review before downstream analysis."
        )


# --------------------------------------------------------------------------- #
# shell / navigation
# --------------------------------------------------------------------------- #
def _goto(step: int) -> None:
    st.session_state.step = max(0, min(step, len(STEPS) - 1))


def _stepper() -> None:
    cols = st.columns(len(STEPS))
    for i, (col, label) in enumerate(zip(cols, STEPS)):
        if i < st.session_state.step:
            col.markdown(f"<span style='color:#1a7f37'>✓ {label}</span>",
                         unsafe_allow_html=True)
        elif i == st.session_state.step:
            col.markdown(f"**🔵 {label}**")
        else:
            col.markdown(f"<span style='color:#8c959f'>{label}</span>",
                         unsafe_allow_html=True)


def _nav() -> None:
    st.divider()
    back, _, fwd = st.columns([1, 4, 1])
    step = st.session_state.step
    if step > 0:
        if back.button("← Back", use_container_width=True):
            _goto(step - 1)
            st.rerun()
    # Step 1 advances via upload; step 2 advances via its own primary button.
    if step not in (0, 1) and step < len(STEPS) - 1:
        if fwd.button("Next →", type="primary", use_container_width=True):
            _goto(step + 1)
            st.rerun()
    if step == 0 and st.session_state.raw is not None:
        if fwd.button("Next →", type="primary", use_container_width=True):
            _goto(1)
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="LabBridge", page_icon="🧪", layout="wide")
    _init_state()

    with st.sidebar:
        st.markdown("### 🧪 LabBridge")
        st.caption("The scientific QC layer that turns clean, ML-ready lab data "
                   "into *trusted* results — with a human in the loop.")
        st.divider()
        if st.button("↺ Start over", use_container_width=True):
            _reset()
            st.rerun()
        st.caption("Deterministic pipeline · no database · no cloud · "
                   "everything stays in this session.")

    st.title("LabBridge")
    st.caption("The scientific QC layer on top of clean lab data — turning "
               "ML-ready data into *trusted* results, with a human in the loop.")
    _stepper()
    st.write("")

    step = st.session_state.step
    needs_result = step in (2, 3, 4, 5)
    if needs_result and st.session_state.result is None:
        st.warning("Run the mapping step first.")
        _goto(min(step, 1))
        st.rerun()

    (step_upload, step_mapping, step_qc, step_qc_intel,
     step_clean, step_export)[step]()
    _nav()


if __name__ == "__main__":
    main()
