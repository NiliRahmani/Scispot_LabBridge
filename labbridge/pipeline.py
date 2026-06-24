"""Headless end-to-end pipeline: messy file in -> clean records + QC + audit out.

This is the engine the future UI will call. It deliberately has no UI concerns;
it returns a structured :class:`PipelineResult` and can also be run as a CLI:

    python -m labbridge.pipeline labbridge/data/messy_plate_long.csv --out out/
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .core import parsers, mapping, normalize, qc
from .core.audit import AuditLog, build_summary, summary_to_markdown
from .core.schema import REQUIRED_FIELD_NAMES, CANONICAL_FIELD_NAMES


@dataclass
class PipelineResult:
    raw: pd.DataFrame
    layout: str
    mappings: list
    clean: pd.DataFrame
    audit: AuditLog
    summary: dict


def run_pipeline(path, overrides: dict[str, str] | None = None) -> PipelineResult:
    """Run ingest -> map -> normalize -> QC -> summary.

    ``overrides`` simulates human-in-the-loop: {source_column: target_field|None}.
    """
    audit = AuditLog()
    raw, layout = parsers.load_table(path)
    rows_in = len(raw)

    mappings = mapping.propose_mappings(raw)
    if overrides:
        by_src = {m.source_column: m for m in mappings}
        for src, target in overrides.items():
            if src in by_src:
                mapping.apply_override(by_src[src], target)

    for m in mappings:
        audit.record("map", field_name=m.target_field, rule=m.signal,
                     detail=m.reason, after=m.target_field, before=m.source_column)

    canonical = mapping.rename_to_canonical(raw, mappings)
    normalized = normalize.normalize_frame(canonical, audit)
    clean = qc.run_qc(normalized, audit)

    # order columns canonically + append a per-row qc_flag column
    clean = _attach_qc_flags(clean, audit)

    required_present = all(f in clean.columns for f in REQUIRED_FIELD_NAMES)
    summary = build_summary(audit, rows_in, len(clean), mappings, required_present)

    return PipelineResult(raw, layout, mappings, clean, audit, summary)


def _attach_qc_flags(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    out = df.copy()
    per_well: dict[str, list[str]] = {}
    for e in audit.flags():
        if e.well:
            per_well.setdefault(e.well, []).append(e.rule)
    if "well_position" in out.columns:
        out["qc_flag"] = out["well_position"].map(
            lambda w: ";".join(sorted(set(per_well.get(str(w), [])))))
    # stable canonical column order, keeping any extras at the end
    ordered = [c for c in CANONICAL_FIELD_NAMES if c in out.columns]
    extras = [c for c in out.columns if c not in ordered and c != "qc_flag"]
    cols = ordered + extras + (["qc_flag"] if "qc_flag" in out.columns else [])
    return out[cols]


def write_outputs(result: PipelineResult, out_dir) -> dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    clean_csv = out_dir / "clean_dataset.csv"
    result.clean.to_csv(clean_csv, index=False)
    paths["clean_csv"] = str(clean_csv)

    summary_json = out_dir / "data_quality_summary.json"
    summary_json.write_text(_json(result.summary), encoding="utf-8")
    paths["summary_json"] = str(summary_json)

    summary_md = out_dir / "data_quality_summary.md"
    summary_md.write_text(summary_to_markdown(result.summary), encoding="utf-8")
    paths["summary_md"] = str(summary_md)

    audit_json = out_dir / "transformation_log.json"
    audit_json.write_text(result.audit.to_json(), encoding="utf-8")
    paths["audit_json"] = str(audit_json)

    return paths


def _json(obj) -> str:
    import json
    return json.dumps(obj, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description="LabBridge headless pipeline")
    ap.add_argument("input", help="path to a messy plate-reader export")
    ap.add_argument("--out", default="out", help="output directory")
    args = ap.parse_args()

    result = run_pipeline(args.input)
    paths = write_outputs(result, args.out)

    s = result.summary
    print(f"layout detected     : {result.layout}")
    print(f"rows in / out       : {s['rows_in']} -> {s['rows_out']}")
    print(f"columns auto/manual : {s['columns_auto_mapped']}/{s['columns_manually_mapped']} "
          f"(review {s['columns_needing_review']}, unmapped {s['columns_unmapped']})")
    print(f"normalizations      : {s['normalizations']}")
    print(f"QC flags            : {s['total_flags']} across {s['wells_flagged']} wells")
    print(f"flags by type       : {s['flags_by_type']}")
    print(f"ML-ready            : {s['ml_ready']}")
    print("outputs:")
    for k, v in paths.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
