"""Regression against the study implementation this package was generalised from.

tests/data/legacy holds synthetic metrics with one engineered case per decision branch,
and the statuses and scores the original scripts produced from them. No real participant
data is involved. The rules engine must reproduce the status of every row and the scoring
engine every value.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quadqc import config, rules, scores

DATA = Path(__file__).parent / "data" / "legacy"


@pytest.fixture(scope="module")
def cfg():
    return config.load(DATA / "config.yaml")


def _metrics(exclude=()):
    table = pd.read_csv(DATA / "metrics.tsv", sep="\t", na_values="n/a")
    table.insert(1, "session_id", "")
    abs_motion = pd.to_numeric(table["eddy_motion_abs_mm"], errors="coerce")
    table.insert(2, "quad_state", np.where(abs_motion.notna(), "complete", "absent"))
    table.insert(3, "exclusion_reason",
                 table["participant_id"].map(lambda p: "operator" if p in exclude else ""))
    return table


def test_every_status_matches(cfg):
    expected = pd.read_csv(DATA / "expected_status.tsv", sep="\t").set_index("subject")
    excluded = set(expected.index[expected["label"] == "EXCLUDED-BAD-INPUT"])
    decided = rules.evaluate(_metrics(excluded), cfg).decisions.set_index("participant_id")
    decided["status"] = decided["status"].replace({"excluded": "fail"})
    assert decided.loc[expected.index, "status"].to_dict() == expected["expected"].to_dict()


def test_fixture_exercises_every_branch():
    labels = set(pd.read_csv(DATA / "expected_status.tsv", sep="\t")["label"])
    assert {"KEEP", "KEEP-WITH-CAVEAT", "CAVEAT-INCOMPLETE-EDDYQC", "DROP", "REPROCESS",
            "NOT-PROCESSED", "NO-IMAGING-DATA", "EXCLUDED-BAD-INPUT"} <= labels


def test_every_score_matches(cfg):
    expected = pd.read_csv(DATA / "expected_scores.tsv", sep="\t", na_values="n/a",
                           keep_default_na=False).set_index("subject")
    metrics = _metrics()
    metrics = metrics[metrics["participant_id"].isin(expected.index)]
    computed = scores.compute(metrics, cfg).scores.set_index("participant_id")
    computed = computed.loc[expected.index]
    numeric = [c for c in expected.columns if c.startswith("qc_")] + [
        "available_family_mean", "overall_is_complete", "n_families_scored", "n_substituted"]
    for column in numeric:
        np.testing.assert_allclose(pd.to_numeric(computed[column]).values,
                                   pd.to_numeric(expected[column]).values,
                                   atol=2e-3, err_msg=column)
    for column in [c for c in expected.columns if c.startswith("cov_")] + ["metric_source"]:
        assert computed[column].tolist() == expected[column].tolist(), column
