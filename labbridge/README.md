# LabBridge

Turn a messy plate-reader / assay export into a **clean, standardized, QC-checked,
ML-ready** dataset — with an explainable column mapping, deterministic quality
checks, and a full transformation audit trail.

This repository is **Phase 1–3**: the headless data pipeline plus a thin
Streamlit demo UI that sits on top of it. No database, no auth, no LLM. The
engine is deterministic and testable; the UI only loads, maps, QCs and exports
by calling the pipeline — it holds no data logic of its own.

## Why it exists

Every lab onboarding starts with the same manual chore: raw instrument files have
inconsistent headers, mixed units, odd date formats, missing wells, overflow
reads, and replicate disagreements. LabBridge automates that first mile and
**shows its work**, so the cleaned data can be trusted and audited.

## Layout

```
labbridge/
├── core/
│   ├── schema.py      # canonical schema + column synonyms + unit tables
│   ├── parsers.py     # file read + layout detection (long-format parser)
│   ├── mapping.py     # column -> canonical mapping with confidence scores
│   ├── normalize.py   # units / decimals / dates / wells / control types
│   ├── qc.py          # validation + robust statistical QC flags
│   └── audit.py       # transformation log + data-quality summary
├── data/
│   └── generate_samples.py  # deterministic clean + messy sample plate
├── tests/             # pytest suite (mapping / normalize / qc / pipeline)
├── pipeline.py        # headless end-to-end engine + CLI
└── app.py             # Phase 3: Streamlit 5-step wizard over the pipeline
```

The core layer is intentionally free of any UI framework.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate on macOS/Linux
```

## Run

```bash
# 1. generate the deterministic sample plate (clean + messy + injected-error log)
python -m labbridge.data.generate_samples

# 2. run the pipeline on the messy export
python -m labbridge.pipeline labbridge/data/messy_plate_long.csv --out labbridge/out

# 3. run the tests
python -m pytest labbridge/tests -q

# 4. launch the demo UI (5-step wizard)
python -m streamlit run labbridge/app.py
```

### Demo UI (Phase 3)

A 5-step wizard for the 2–3 minute demo video:

1. **Upload** — drop an export or click *Load sample plate export*; the detected
   file layout is shown.
2. **Schema mapping** — every column with its confidence score and green/yellow/red
   bucket. `Anlyst` lands yellow (~78%, fuzzy → `operator`) for the human
   confirm/override moment.
3. **QC & anomalies** — a plate heatmap (▲ marks flagged wells) beside a table of
   flags with severity, well, and reason.
4. **Clean preview** — before → after for every normalized value, then the full
   clean table.
5. **Export & report** — download the clean CSV, the quality summary, and the
   transformation log; the summary renders inline.

Everything lives in Streamlit session state — no database, nothing persisted.

Outputs land in `labbridge/out/`:

- `clean_dataset.csv` — canonical, normalized records + a per-well `qc_flag` column
- `data_quality_summary.md` / `.json` — the one-page summary
- `transformation_log.json` — every change and flag, with before/after

## What the pipeline does

1. **Detect layout** — long vs grid (grid parser is a Phase-4 stub).
2. **Map columns** — exact / synonym / fuzzy / content-inference, each with a
   confidence score and a green / yellow / red review bucket. Contention between
   columns is resolved automatically; human overrides lock a mapping.
3. **Normalize** — µM/mM → nM, decimal commas, embedded units, mixed date formats
   → ISO 8601, well coordinates (`a01` → `A1`), control-type spellings.
4. **QC flag** — missing / overflow / negative / invalid-well / duplicate-well,
   robust MAD outliers, replicate-CV disagreement, control sanity.
5. **Audit + summarize** — nothing changes silently; everything is logged.

## Deliberate scope decisions

- **Plate edge-effect detection is deferred to Phase 4.** With few, heterogeneous
  samples per plate, outer-vs-interior comparison is not statistically reliable;
  flagging it would be false confidence. Proper detection needs a uniformity
  plate or row/column detrending.
- **No LLM.** Mapping is deterministic and explainable. An LLM fallback for
  genuinely ambiguous headers is a later, optional, human-confirmed add-on.

## Sample data

`generate_samples.py` builds a 96-well plate (standard curve + controls + 24
triplicate samples) and a dirty long-format export with a fixed set of injected
problems recorded in `injected_errors.json`, so tests can assert the pipeline
catches what was planted.
