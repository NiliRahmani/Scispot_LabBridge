"""Column mapping with explainable confidence scores.

Deterministic, no LLM. For every raw column we compute a confidence that it
maps to each canonical field, taking the *max* of three signals:

* exact / synonym match on the (normalized) header name
* fuzzy header similarity (rapidfuzz token-set ratio)
* content inference -- what the column's *values* look like

The blend is explainable: each proposal carries the signal that produced it and
a human-readable reason. Thresholds drive the green/yellow/red review buckets
the UI will eventually show.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from rapidfuzz import fuzz

from .schema import (
    CANONICAL_FIELDS,
    FieldKind,
    CanonicalField,
)

# Confidence thresholds (also referenced by the UI for colour-coding).
AUTO_THRESHOLD = 0.85      # green: map without asking
REVIEW_THRESHOLD = 0.60    # yellow: propose but ask the human
# < REVIEW_THRESHOLD -> red: leave unmapped

_PUNCT_RE = re.compile(r"[^\w\s]")
_WELL_RE = re.compile(r"^\s*[A-Ha-h]0?(1[0-2]|[1-9])\s*$")
_NUMERIC_RE = re.compile(r"^\s*-?\d+([.,]\d+)?\s*[a-zA-Zµμ%/]*\s*$")


def normalize_header(name: str) -> str:
    """lowercase, strip punctuation, collapse whitespace."""
    s = _PUNCT_RE.sub(" ", str(name).lower())
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class ColumnMapping:
    source_column: str
    target_field: Optional[str]    # canonical name, or None if unmapped
    confidence: float              # 0..1
    signal: str                    # "exact" | "synonym" | "fuzzy" | "content" | "none"
    reason: str
    bucket: str                    # "auto" | "review" | "unmapped"
    locked: bool = False           # set True when a human overrides

    def as_dict(self) -> dict:
        return {
            "source_column": self.source_column,
            "target_field": self.target_field,
            "confidence": round(self.confidence, 3),
            "signal": self.signal,
            "reason": self.reason,
            "bucket": self.bucket,
            "locked": self.locked,
        }


# --- content inference ------------------------------------------------------

def _fraction(series: pd.Series, predicate) -> float:
    vals = series.dropna().astype(str)
    vals = vals[vals.str.strip() != ""]
    if len(vals) == 0:
        return 0.0
    return float(vals.head(60).map(predicate).mean())


def _content_scores(series: pd.Series) -> dict[str, float]:
    """Confidence contributed by the column's *values* per canonical field."""
    scores: dict[str, float] = {}

    well_frac = _fraction(series, lambda v: bool(_WELL_RE.match(v)))
    if well_frac:
        scores["well_position"] = 0.6 + 0.39 * well_frac  # strong, distinctive signal

    numeric_frac = _fraction(series, lambda v: bool(_NUMERIC_RE.match(v)))
    if numeric_frac:
        scores["measurement_value"] = 0.45 + 0.25 * numeric_frac

    def _is_date(v: str) -> bool:
        from dateutil import parser as dparser
        try:
            dparser.parse(v, fuzzy=False)
            return any(ch.isdigit() for ch in v) and (("/" in v) or ("-" in v) or (":" in v))
        except (ValueError, OverflowError):
            return False

    date_frac = _fraction(series, _is_date)
    if date_frac:
        scores["timestamp"] = 0.5 + 0.4 * date_frac

    return scores


# --- per-field name scoring -------------------------------------------------

def _name_score(field: CanonicalField, norm_header: str) -> tuple[float, str]:
    if norm_header == field.name.replace("_", " ") or norm_header == field.name:
        return 1.0, "exact"
    for syn in field.synonyms:
        if norm_header == normalize_header(syn):
            return 0.92, "synonym"
    # fuzzy against name + synonyms; scale into a sub-synonym band
    candidates = [field.name.replace("_", " ")] + [normalize_header(s) for s in field.synonyms]
    best = max(fuzz.token_set_ratio(norm_header, c) for c in candidates) / 100.0
    return best * 0.85, "fuzzy"


def _bucket(conf: float) -> str:
    if conf >= AUTO_THRESHOLD:
        return "auto"
    if conf >= REVIEW_THRESHOLD:
        return "review"
    return "unmapped"


def propose_mappings(df: pd.DataFrame) -> list[ColumnMapping]:
    """Propose a canonical mapping for every source column.

    Resolves contention: if two source columns claim the same target, the
    higher-confidence one keeps it and the loser is demoted to its next-best
    target (or unmapped).
    """
    # 1) best candidate per source column
    proposals: list[ColumnMapping] = []
    for col in df.columns:
        norm = normalize_header(col)
        content = _content_scores(df[col])

        best_field, best_conf, best_signal = None, 0.0, "none"
        for field in CANONICAL_FIELDS:
            name_conf, name_sig = _name_score(field, norm)
            cont_conf = content.get(field.name, 0.0)
            if cont_conf > name_conf:
                conf, sig = cont_conf, "content"
            else:
                conf, sig = name_conf, name_sig
            # small agreement bonus when name and content concur
            if cont_conf >= REVIEW_THRESHOLD and name_conf >= REVIEW_THRESHOLD:
                conf = min(1.0, conf + 0.05)
            if conf > best_conf:
                best_field, best_conf, best_signal = field.name, conf, sig

        reason = _reason(col, best_field, best_signal, best_conf)
        proposals.append(ColumnMapping(
            source_column=col, target_field=best_field, confidence=best_conf,
            signal=best_signal, reason=reason, bucket=_bucket(best_conf),
        ))

    _resolve_contention(proposals, df)
    return proposals


def _reason(col: str, field: Optional[str], signal: str, conf: float) -> str:
    if field is None or conf < REVIEW_THRESHOLD:
        return f"'{col}' did not confidently match any canonical field."
    if signal == "exact":
        return f"Header '{col}' exactly matches '{field}'."
    if signal == "synonym":
        return f"Header '{col}' is a known synonym for '{field}'."
    if signal == "content":
        return f"Values in '{col}' look like '{field}'."
    return f"Header '{col}' is similar to '{field}' (fuzzy match)."


def _resolve_contention(proposals: list[ColumnMapping], df: pd.DataFrame) -> None:
    """Ensure at most one source column maps to each canonical field."""
    by_target: dict[str, list[ColumnMapping]] = {}
    for p in proposals:
        if p.target_field is not None and p.bucket != "unmapped":
            by_target.setdefault(p.target_field, []).append(p)

    for target, claimants in by_target.items():
        if len(claimants) <= 1:
            continue
        claimants.sort(key=lambda m: m.confidence, reverse=True)
        for loser in claimants[1:]:
            # demote: recompute next-best target excluding the contested one
            next_field, next_conf, next_sig = _second_best(loser.source_column, df, exclude=target)
            loser.target_field = next_field
            loser.confidence = next_conf
            loser.signal = next_sig if next_field else "none"
            loser.bucket = _bucket(next_conf) if next_field else "unmapped"
            loser.reason = _reason(loser.source_column, next_field, loser.signal, next_conf)


def _second_best(col: str, df: pd.DataFrame, exclude: str) -> tuple[Optional[str], float, str]:
    norm = normalize_header(col)
    content = _content_scores(df[col])
    best_field, best_conf, best_signal = None, 0.0, "none"
    for field in CANONICAL_FIELDS:
        if field.name == exclude:
            continue
        name_conf, name_sig = _name_score(field, norm)
        cont_conf = content.get(field.name, 0.0)
        conf, sig = (cont_conf, "content") if cont_conf > name_conf else (name_conf, name_sig)
        if conf > best_conf:
            best_field, best_conf, best_signal = field.name, conf, sig
    if best_conf < REVIEW_THRESHOLD:
        return None, best_conf, "none"
    return best_field, best_conf, best_signal


def apply_override(mapping: ColumnMapping, target_field: Optional[str]) -> ColumnMapping:
    """Return a human-locked mapping (confidence 1.0)."""
    mapping.target_field = target_field
    mapping.confidence = 1.0 if target_field else 0.0
    mapping.signal = "manual"
    mapping.bucket = "auto" if target_field else "unmapped"
    mapping.locked = True
    mapping.reason = (f"Manually mapped to '{target_field}'." if target_field
                      else "Manually left unmapped.")
    return mapping


def rename_to_canonical(df: pd.DataFrame, mappings: list[ColumnMapping]) -> pd.DataFrame:
    """Project + rename a raw DataFrame to canonical columns using the mappings."""
    rename = {m.source_column: m.target_field for m in mappings
              if m.target_field and m.bucket != "unmapped"}
    out = df[list(rename.keys())].rename(columns=rename)
    # collapse any accidental duplicate canonical columns, keeping the first
    out = out.loc[:, ~out.columns.duplicated()]
    return out
