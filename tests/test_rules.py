import numpy as np
import pandas as pd
import pytest

from quadqc import config, rules
from quadqc.config import Rule


def _cfg(**over):
    return config.from_dict(over, __import__("pathlib").Path("."))


def _metrics(**cols):
    n = len(next(iter(cols.values())))
    table = pd.DataFrame({
        "participant_id": [f"sub-{i:03d}" for i in range(n)], "session_id": "",
        "quad_state": "complete", "exclusion_reason": "", **cols})
    return table


GOOD = dict(eddy_motion_abs_mm=1.0, eddy_motion_rel_mm=0.5, eddy_outliers_pct_tot=0.5,
            eddy_snr_b0=20.0, eddy_n_pe_dirs=2, eddy_vox_mm=2.0)


def _one(cfg=None, **over):
    row = {**GOOD, **over}
    table = _metrics(**{k: [v] for k, v in row.items() if k not in ("quad_state",
                                                                    "exclusion_reason")})
    for key in ("quad_state", "exclusion_reason"):
        if key in over:
            table[key] = over[key]
    return rules.evaluate(table, cfg or _cfg()).decisions.iloc[0]


def test_clean_participant_passes():
    assert _one().status == "pass"


@pytest.mark.parametrize("over,status,by", [
    (dict(eddy_motion_abs_mm=2.5), "caveat", "review_threshold"),
    (dict(eddy_motion_abs_mm=4.5), "fail", "severe_threshold"),
    (dict(eddy_motion_rel_mm=2.5), "fail", "severe_threshold"),
    (dict(eddy_outliers_pct_tot=2.5), "caveat", "review_threshold"),
    (dict(eddy_snr_b0=4.0), "fail", "severe_threshold"),
    (dict(eddy_n_pe_dirs=1), "caveat", "review_threshold"),
    (dict(eddy_snr_b0=np.nan), "incomplete", "missing_required_metric"),
    (dict(quad_state="absent"), "missing", "no_quad_output"),
    (dict(quad_state="failed"), "missing", "no_quad_output"),
    (dict(quad_state="invalid"), "missing", "no_quad_output"),
    (dict(exclusion_reason="wrong scan"), "excluded", "exclusion"),
])
def test_decision_order(over, status, by):
    row = _one(**over)
    assert (row.status, row.decided_by) == (status, by)


def test_thresholds_are_strict_and_scale_with_voxel_size():
    assert _one(eddy_motion_abs_mm=2.0, eddy_vox_mm=2.0).status == "pass"  # exactly 1 voxel
    assert _one(eddy_motion_abs_mm=2.0, eddy_vox_mm=1.0).status == "caveat"
    assert _one(eddy_motion_abs_mm=3.0, eddy_vox_mm=1.0).status == "fail"


def test_voxel_rule_without_voxel_size_is_not_a_pass():
    row = _one(eddy_vox_mm=np.nan)
    assert row.status == "incomplete"
    assert "eddy_motion_abs_mm" in row.missing_metrics


def test_convergence_needs_three_absolute_domains():
    assert _one(eddy_motion_abs_mm=2.5, eddy_outliers_pct_tot=2.5).status == "caveat"
    row = _one(eddy_motion_abs_mm=2.5, eddy_outliers_pct_tot=2.5, eddy_snr_b0=9.0)
    assert (row.status, row.decided_by) == ("fail", "convergence")


def test_two_flags_in_one_domain_do_not_converge():
    row = _one(eddy_motion_abs_mm=2.5, eddy_motion_rel_mm=1.2, eddy_outliers_pct_tot=2.5)
    assert row.status == "caveat"


def test_severe_outranks_missing_metric():
    assert _one(eddy_motion_abs_mm=4.5, eddy_snr_b0=np.nan).status == "fail"


def test_excluded_rows_keep_their_evaluations():
    table = _metrics(**{k: [v] for k, v in GOOD.items()})
    table["exclusion_reason"] = "wrong scan"
    out = rules.evaluate(table, _cfg())
    assert out.decisions.iloc[0].status == "excluded"
    assert len(out.evaluations) > 0


def _relative_cfg(z=5.0, **rule):
    return _cfg(rules=[dict(metric="m", kind="relative", domain="d", z=z, **rule)])


def test_relative_rule_raises_caveat_only():
    values = list(np.linspace(1.0, 2.0, 30)) + [50.0]
    out = rules.evaluate(_metrics(m=values), _relative_cfg())
    assert out.decisions.iloc[-1].status == "caveat"
    assert out.decisions.iloc[0].status == "pass"
    assert "fail" not in set(out.decisions.status)


def test_relative_z_uses_median_and_mad():
    values = np.linspace(1.0, 2.0, 30)
    out = rules.evaluate(_metrics(m=values), _relative_cfg(z=99.0))
    median = np.median(values)
    mad = np.median(np.abs(values - median)) * 1.4826
    first = out.evaluations.iloc[0]
    assert first.z == pytest.approx((values[0] - median) / mad, abs=1e-3)


def test_small_cohort_relative_rule_is_skipped_with_a_warning():
    out = rules.evaluate(_metrics(m=[1.0, 2.0, 3.0]), _relative_cfg())
    assert set(out.evaluations.level) == {"not_evaluated"}
    assert set(out.decisions.status) == {"pass"}  # cohort size is not a fault of the participant
    assert any("not evaluated" in w for w in out.warnings)


def test_relative_rule_missing_value_is_still_incomplete():
    values = list(np.linspace(1.0, 2.0, 30)) + [np.nan]
    out = rules.evaluate(_metrics(m=values), _relative_cfg())
    assert out.decisions.iloc[-1].status == "incomplete"


def test_excluded_rows_do_not_shape_the_reference():
    values = list(np.linspace(1.0, 2.0, 30))
    table = _metrics(m=values + [1000.0] * 40)
    table.loc[30:, "exclusion_reason"] = "wrong scan"
    out = rules.evaluate(table, _relative_cfg())
    median = np.median(values)
    assert out.evaluations.iloc[0].z == pytest.approx(
        (values[0] - median) / (np.median(np.abs(np.array(values) - median)) * 1.4826), abs=1e-3)


def test_wildcard_rules_expand_per_shell():
    out = rules.evaluate(
        _metrics(eddy_cnr_b700=np.linspace(1, 2, 25), eddy_cnr_b3000=np.linspace(2, 3, 25)),
        _cfg(rules=[dict(metric="eddy_cnr_b*", kind="relative", domain="s", z=5.0,
                         direction="low")]))
    assert set(out.evaluations.metric) == {"eddy_cnr_b700", "eddy_cnr_b3000"}


FALLBACK = {"fallback": {"enabled": True}}


def _fb(**over):
    base = dict(preproc_b0_snr_repeat=20.0, motion_rms_mean_mm=0.5, motion_rel_mean_mm=0.4,
                bvecrot_rms_deg=0.2)
    return _one(_cfg(**FALLBACK), quad_state="failed", **{**base, **over})


def test_fallback_is_off_by_default():
    assert _one(quad_state="failed", preproc_b0_snr_repeat=20.0, motion_rms_mean_mm=0.5,
                motion_rel_mean_mm=0.4).status == "missing"


def test_fallback_can_never_produce_a_pass():
    row = _fb()
    assert (row.status, row.pathway) == ("caveat_incomplete_quad", "fallback")


def test_fallback_can_fail_on_measured_rotation():
    assert _fb(bvecrot_rms_deg=2.5).status == "fail"


def test_fallback_without_evidence_is_missing():
    row = _fb(preproc_b0_snr_repeat=np.nan)
    assert (row.status, row.decided_by) == ("missing", "insufficient_fallback_evidence")


def test_fallback_does_not_apply_to_participants_with_quad():
    row = _one(_cfg(**FALLBACK), bvecrot_rms_deg=9.0)
    assert (row.status, row.pathway) == ("pass", "quad")


def test_every_evaluation_is_traceable():
    out = rules.evaluate(_metrics(**{k: [v] for k, v in GOOD.items()}), _cfg())
    assert {"metric", "value", "review_threshold", "severe_threshold", "level"} <= set(
        out.evaluations.columns)
    assert len(out.rule_summary) == out.evaluations.metric.nunique()


def test_rule_headroom_marks_unreachable_thresholds():
    table = _metrics(m=np.linspace(1, 2, 10))
    out = rules.evaluate(table, _cfg(rules=[dict(metric="m", domain="d", review=100.0)]))
    assert out.rule_summary.iloc[0].headroom_sd > 10


def test_rule_dataclass_defaults_are_screening_only():
    assert Rule(metric="x", domain="d", review=1).severe is None
