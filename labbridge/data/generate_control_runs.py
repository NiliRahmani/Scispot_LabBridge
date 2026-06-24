"""Deterministic multi-run control history for QC-intelligence monitoring.

Honest premise: a lab runs the *same* positive/negative controls on every plate
or every day, so tracking those controls over time is real, standard QC practice
(Levey-Jennings / Westgard). This produces a small control series with a
deliberately injected downstream **reagent drift** so the control-chart logic has
a real violation to catch.

Output: ``control_runs.csv`` next to this file.

Run:  python -m labbridge.data.generate_control_runs
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20260623

N_RUNS = 24
BASELINE_RUNS = 16          # first runs are in-control; limits are set from these
POS_TARGET, POS_SD = 60.0, 3.0
NEG_TARGET, NEG_SD = 0.20, 0.05
DRIFT_PER_RUN = 2.3         # nM/run downward drift after the baseline period


def build() -> list[dict]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    start = date(2026, 5, 18)
    for i in range(N_RUNS):
        d = (start + timedelta(days=i)).isoformat()
        if i < BASELINE_RUNS:
            pos = rng.gauss(POS_TARGET, POS_SD)
        else:
            # progressive reagent degradation: controls trend downward
            pos = rng.gauss(POS_TARGET - DRIFT_PER_RUN * (i - BASELINE_RUNS + 1), POS_SD)
        neg = abs(rng.gauss(NEG_TARGET, NEG_SD))
        for ctrl, val in (("positive", pos), ("negative", neg)):
            rows.append({
                "run_id": f"RUN_{i + 1:03d}",
                "run_date": d,
                "control_type": ctrl,
                "analyte": "IL6",
                "value": round(val, 3),
                "unit": "nM",
                "instrument_id": "READER-A12",
            })
    return rows


def main() -> None:
    rows = build()
    path = HERE / "control_runs.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"control runs: {len(rows)} rows ({N_RUNS} runs) -> {path.name}")


if __name__ == "__main__":
    main()
