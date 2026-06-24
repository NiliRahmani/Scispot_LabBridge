# 🧪 LabBridge

**Turn a messy plate-reader / assay export into a clean, standardized, QC-checked,
ML-ready dataset — with an explainable column mapping, deterministic quality
checks, automatic scientific validation, and a full audit trail.**

> ▶️ **[2-min demo video](REPLACE_WITH_YOUTUBE_OR_LOOM_LINK)** &nbsp;·&nbsp;
> 🔗 **[Try it live (no install)](REPLACE_WITH_STREAMLIT_LINK)** &nbsp;·&nbsp;
> 💻 Code + 32 passing tests below

<!-- Optional but recommended: add a screenshot of the Control monitoring / plate map
     and reference it here, e.g.:  ![LabBridge](docs/screenshot.png) -->

---

## The problem it solves

Every lab-software onboarding starts with the same manual chore: raw instrument
files arrive with inconsistent headers, mixed units, odd date formats, missing
wells, overflow reads, and replicate disagreements. Someone cleans that by hand,
and the cleaned data is trusted on faith.

LabBridge automates that first mile **and shows its work** — so the cleaned data
can be audited, and so the *scientific* validity of the data (not just its tidiness)
is checked before anyone models on it.

## What it does

1. **Detect layout** — long vs grid plate formats.
2. **Map columns** — exact / synonym / fuzzy / content-inference, each with a
   confidence score and a green / yellow / red review bucket. Ambiguous columns
   (e.g. `Anlyst` → `operator`, ~78%) are surfaced for a human to confirm — you
   stay in control of every column.
3. **Normalize** — µM/mM → nM, decimal commas, embedded units, mixed date formats
   → ISO 8601, well coordinates (`a01` → `A1`), control-type spellings.
4. **QC flag** — missing / overflow / negative / invalid-well / duplicate-well,
   robust MAD outliers, replicate-CV disagreement, control sanity.
5. **QC Intelligence** — standard-curve fit, replicate reliability, anomaly
   detection, and Levey-Jennings control monitoring with Westgard rules, ending
   in a fitness verdict: *clean data is necessary, but only validated data is
   safe to model on.*
6. **Audit + summarize** — nothing changes silently; every transformation and
   flag is logged with before/after.

No database, no auth, no LLM. The engine is **deterministic and testable**; the
Streamlit UI is a thin 6-step wizard that only calls the pipeline.

## Run it locally (2 commands)

```bash
pip install -r requirements.txt
streamlit run labbridge/app.py
```

Then click **Load sample plate export** in the UI — a 96-well plate with realistic,
deliberately-injected data issues.

### Run the tests

```bash
pytest labbridge/tests -q          # 32 passing
```

### Headless pipeline / CLI

```bash
python -m labbridge.pipeline labbridge/data/messy_plate_long.csv --out out/
```

## Layout

```
labbridge/
├── core/
│   ├── schema.py      # canonical schema + column synonyms + unit tables
│   ├── parsers.py     # file read + layout detection
│   ├── mapping.py     # column -> canonical mapping with confidence scores
│   ├── normalize.py   # units / decimals / dates / wells / control types
│   ├── qc.py          # validation + robust statistical QC flags
│   ├── qc_intel.py    # standard curve, replicates, control monitoring, verdict
│   └── audit.py       # transformation log + data-quality summary
├── data/              # deterministic sample plate + injected-error log
├── tests/             # pytest suite (mapping / normalize / qc / pipeline)
├── pipeline.py        # headless end-to-end engine + CLI
└── app.py             # Streamlit 6-step wizard over the pipeline
```

## Deliberate scope decisions

- **Plate edge-effect detection is deferred.** With few, heterogeneous samples per
  plate, outer-vs-interior comparison isn't statistically reliable; flagging it
  would be false confidence. Proper detection needs a uniformity plate or
  row/column detrending.
- **No LLM.** Mapping is deterministic and explainable. An LLM fallback for
  genuinely ambiguous headers is a later, optional, human-confirmed add-on.

## License

MIT — see [LICENSE](LICENSE).
