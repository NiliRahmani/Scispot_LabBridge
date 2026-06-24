"""Tests for column mapping + confidence scoring."""

import pandas as pd

from labbridge.core import mapping
from labbridge.core.mapping import AUTO_THRESHOLD, REVIEW_THRESHOLD


def _df():
    return pd.DataFrame({
        "Well": ["A1", "B2", "C3"],
        "Sample Name": ["S1", "S2", "S3"],
        "Conc.": ["1.2", "3.4", "5.6"],
        "Read Date/Time": ["2026-06-20", "2026-06-20", "2026-06-20"],
        "Mystery": ["x", "y", "z"],
    })


def test_exact_and_synonym_map_high_confidence():
    maps = {m.source_column: m for m in mapping.propose_mappings(_df())}
    assert maps["Well"].target_field == "well_position"
    assert maps["Well"].confidence >= AUTO_THRESHOLD
    assert maps["Sample Name"].target_field == "sample_id"
    assert maps["Conc."].target_field == "measurement_value"


def test_content_inference_promotes_well_column():
    # header is unhelpful, but values look like wells
    df = pd.DataFrame({"loc": ["A1", "H12", "D6", "B7"]})
    m = mapping.propose_mappings(df)[0]
    assert m.target_field == "well_position"
    assert m.signal in ("content", "synonym", "exact")


def test_unknown_column_is_unmapped_or_low():
    maps = {m.source_column: m for m in mapping.propose_mappings(_df())}
    mystery = maps["Mystery"]
    assert mystery.bucket == "unmapped" or mystery.confidence < REVIEW_THRESHOLD


def test_no_two_columns_share_a_target():
    df = pd.DataFrame({"Well": ["A1"], "Well ID": ["A1"]})  # both want well_position
    maps = mapping.propose_mappings(df)
    targets = [m.target_field for m in maps if m.bucket != "unmapped"]
    assert len(targets) == len(set(targets))


def test_override_locks_mapping():
    maps = mapping.propose_mappings(_df())
    m = maps[0]
    mapping.apply_override(m, "operator")
    assert m.locked and m.target_field == "operator" and m.confidence == 1.0
