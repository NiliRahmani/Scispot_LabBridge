"""Generate deterministic plate-reader sample data for the LabBridge demo.

Produces three artifacts in this directory:

* ``clean_plate_001.csv``    -- canonical, "what the data should be" ground truth.
* ``messy_plate_long.csv``   -- Variant B: a realistic dirty long-format export.
* ``injected_errors.json``   -- a machine-readable record of every corruption we
                                introduced, so QC tests can assert recall and the
                                demo can honestly say "we knew these were here".

A fixed RNG seed keeps the output stable across runs and machines.

Run:  python -m labbridge.data.generate_samples
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROWS = "ABCDEFGH"
COLS = list(range(1, 13))
SEED = 20260623
HERE = Path(__file__).resolve().parent

# Standard curve: 8 nominal concentrations (nM), laid down columns 1 & 2 (duplicate).
STANDARD_NM = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0]


def well(row: str, col: int) -> str:
    return f"{row}{col}"


def build_clean() -> list[dict]:
    """Build the intended, clean canonical records for one 96-well plate.

    Layout:
      * cols 1-2 : standard curve (8 levels) in duplicate
      * col  3   : controls (positive / negative / blank)
      * cols 4-12: 24 patient samples, each in triplicate. Replicates share a
                   sample_id and sit in 3 consecutive columns of the same row,
                   so replicate-level QC has real groups to work with.
    """
    rng = random.Random(SEED)
    records: list[dict] = []
    base_ts = "2026-06-20T09:14:00"

    # per-sample "true" concentration so triplicates agree by design
    true_conc: dict[str, float] = {}

    for col in COLS:
        for ri, row in enumerate(ROWS):
            w = well(row, col)
            rec = {
                "plate_id": "PLATE_001",
                "well_position": w,
                "sample_id": "",
                "control_type": "sample",
                "analyte": "IL6",
                "measurement_value": None,
                "measurement_unit": "nM",
                "replicate": 1,
                "concentration_expected": "",
                "timestamp": base_ts,
                "operator": "JLEE",
                "instrument_id": "READER-A12",
            }

            if col in (1, 2):  # standard curve in duplicate
                conc = STANDARD_NM[ri]
                rec["control_type"] = "standard"
                rec["sample_id"] = f"STD_{ri + 1}"
                rec["replicate"] = 1 if col == 1 else 2
                rec["concentration_expected"] = conc
                rec["measurement_value"] = round(conc * rng.uniform(0.97, 1.03), 3)
            elif col == 3:  # controls
                roles = {
                    "A": ("positive", 60.0), "B": ("positive", 60.0),
                    "C": ("negative", 0.2), "D": ("negative", 0.2),
                    "E": ("blank", 0.05), "F": ("blank", 0.05),
                    "G": ("blank", 0.05), "H": ("blank", 0.05),
                }
                role, target = roles[row]
                rec["control_type"] = role
                rec["sample_id"] = {"positive": "PC", "negative": "NC", "blank": "BLANK"}[role]
                if role == "blank":
                    rec["measurement_value"] = round(abs(rng.gauss(0.05, 0.02)), 3)
                else:
                    rec["measurement_value"] = round(target * rng.uniform(0.9, 1.1), 3)
            else:  # patient samples in triplicate
                group = (col - 4) // 3          # 0,1,2
                rep = (col - 4) % 3 + 1          # 1,2,3
                sample_no = group * 8 + ri + 1   # 1..24
                sid = f"S{sample_no:03d}"
                rec["sample_id"] = sid
                rec["replicate"] = rep
                if sid not in true_conc:
                    # samples on one assay run cluster in a comparable range,
                    # which is what makes a positional edge effect detectable.
                    true_conc[sid] = max(2.0, rng.gauss(20.0, 4.0))
                # replicates agree within ~3%
                rec["measurement_value"] = round(true_conc[sid] * rng.uniform(0.97, 1.03), 3)

            records.append(rec)
    return records


def corrupt(clean: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (messy_rows, injected_errors) derived from clean records.

    Messy rows use messy *header names*; values are stringified and corrupted.
    """
    rng = random.Random(SEED + 1)
    injected: list[dict] = []
    messy: list[dict] = []

    by_well = {r["well_position"]: r for r in clean}

# NOTE: A synthetic plate edge-effect was intentionally NOT injected. With only
# 24 heterogeneous samples on the plate, outer-vs-interior median comparison is
# not statistically reliable, and flagging it anyway would be false confidence.
# Robust edge-effect detection (uniformity plate or row/column detrending) is a
# Phase-4 item; Phase 1 only ships checks that are defensible on this data.

    # --- Build the messy long rows with corruptions ---
    # Targeted single-well corruptions:
    missing_wells = {"D5", "F9"}            # blank / N/A / ND
    overflow_wells = {"B7"}                 # OVRFLW token
    negative_wells = {"G6"}                 # negative value
    umol_wells = {"E8", "E9"}               # reported in micromolar
    decimal_comma_wells = {"C10", "D10"}    # european decimal
    embedded_unit_wells = {"H4", "H5"}      # "12.3 nM" inside the value cell
    duplicate_source = "A6"                 # will emit a duplicate row for this well
    blank_high_well = "E3"                  # a blank with a high reading -> control fail
    replicate_off_well = "B4"               # bump one replicate to break CV

    # date format variety, cycled across rows
    def fmt_date(i: int) -> str:
        choices = [
            "2026-06-20 09:14",          # iso-ish
            "06/20/2026 9:14 AM",        # US
            "20-Jun-2026 09:14",         # day-mon-year
            "2026/06/20",                # slash iso
        ]
        return choices[i % len(choices)]

    # control-type spelling variety
    ctrl_spelling = {
        "sample": ["Sample", "Unknown", "sample "],
        "positive": ["Pos", "Positive Control", "PC"],
        "negative": ["NEG", "neg ctrl", "Negative"],
        "blank": ["Blank", "blk", "buffer"],
        "standard": ["Std", "Standard", "CAL"],
    }

    for i, r in enumerate(clean):
        w = r["well_position"]
        val = r["measurement_value"]
        unit = "nM"
        notes = []

        # value string assembly with targeted corruptions
        if w in missing_wells:
            val_str = rng.choice(["", "N/A", "ND", "-"])
            injected.append({"well": w, "type": "MISSING_VALUE"})
        elif w in overflow_wells:
            val_str = "OVRFLW"
            injected.append({"well": w, "type": "SATURATED_OR_OVERFLOW"})
        elif w in negative_wells:
            val_str = str(round(-abs(rng.uniform(0.1, 1.0)), 3))
            injected.append({"well": w, "type": "NEGATIVE_VALUE"})
        elif w in umol_wells and val is not None:
            unit = rng.choice(["uM", "µM"])
            val_str = repr(round(val / 1000.0, 6))  # same physical qty, different unit
            injected.append({"well": w, "type": "UNIT_NORMALIZED", "from": unit})
        elif w in decimal_comma_wells and val is not None:
            val_str = str(val).replace(".", ",")
            injected.append({"well": w, "type": "DECIMAL_NORMALIZED"})
        elif w in embedded_unit_wells and val is not None:
            val_str = f"{val} nM"
            injected.append({"well": w, "type": "EMBEDDED_UNIT"})
        elif w == blank_high_well:
            val_str = "55.0"  # a "blank" reading like a positive
            injected.append({"well": w, "type": "CONTROL_FAIL", "detail": "blank reads high"})
        elif w == replicate_off_well and val is not None:
            val_str = str(round(val * 4.0, 3))  # break replicate agreement
            injected.append({"well": w, "type": "REPLICATE_CV_HIGH"})
        elif val is None:
            val_str = ""
        else:
            val_str = str(val)

        row_out = {
            "Plate": "PLATE_001" if i % 7 else "plate_001",  # casing wobble
            "Well": w,
            "Sample Name": r["sample_id"],
            "Type": rng.choice(ctrl_spelling[r["control_type"]]),
            "Analyte": r["analyte"],
            "Conc.": val_str,
            "Unit": unit,
            "Rep": r["replicate"],
            "Expected": r["concentration_expected"],
            "Read Date/Time": fmt_date(i),
            # Intentionally abbreviated header: maps to `operator` only by fuzzy
            # similarity (~0.78), so the UI surfaces a yellow "needs review"
            # proposal and a human confirm/override moment.
            "Anlyst": r["operator"],
            "Instrument": r["instrument_id"],
        }
        messy.append(row_out)

        if w == duplicate_source:
            dup = dict(row_out)
            messy.append(dup)
            injected.append({"well": w, "type": "DUPLICATE_WELL"})

    # shuffle row order a little (real exports aren't always sorted)
    rng.shuffle(messy)
    return messy, injected


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main() -> None:
    clean = build_clean()
    messy, injected = corrupt(clean)

    clean_fields = list(clean[0].keys())
    write_csv(HERE / "clean_plate_001.csv", clean, clean_fields)

    messy_fields = list(messy[0].keys())
    write_csv(HERE / "messy_plate_long.csv", messy, messy_fields)

    (HERE / "injected_errors.json").write_text(
        json.dumps(injected, indent=2), encoding="utf-8"
    )

    print(f"clean rows : {len(clean)} -> clean_plate_001.csv")
    print(f"messy rows : {len(messy)} -> messy_plate_long.csv")
    print(f"injected   : {len(injected)} issues -> injected_errors.json")


if __name__ == "__main__":
    main()
