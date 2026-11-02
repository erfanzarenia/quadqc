import numpy as np
import pandas as pd
import pytest

from quadqc import config, scores


def _cfg(**over):
    return config.from_dict(over, __import__("pathlib").Path("."))


def _table(**cols):
    n = len(next(iter(cols.values())))
    return pd.DataFrame({"participant_id": [f"sub-{i:03d}" for i in range(n)], "session_id": "",
                         "quad_state": "complete", "exclusion_reason": "", **cols})


ONE_FAMILY = dict(scoring=dict(min_families=1, core_families=[], families={
    "f": {"label": "F", "slots": [dict(name="a", metric="a"), dict(name="b", metric="b",
                                                                   direction="low")]}}))


def test_robust_z_definition():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
    z, degenerate = scores.robust_z(values, pd.Series(True, index=values.index))
    assert not degenerate
    assert z.iloc[2] == 0
    assert z.iloc[3] == pytest.approx(1 / 1.4826)


def test_zero_mad_falls_back_to_sd_and_warns():
    table = _table(a=[1.0] * 20 + [9.0], b=np.arange(21.0))
    out = scores.compute(table, _cfg(**ONE_FAMILY))
    assert any("zero MAD" in w for w in out.warnings)


def test_direction_and_winsor():
    values = pd.Series(np.arange(1.0, 22.0))
    ref = pd.Series(True, index=values.index)
    high, _ = scores.directed_z(values, "high", ref, 5.0)
    low, _ = scores.directed_z(values, "low", ref, 5.0)
    assert high.iloc[-1] == pytest.approx(-low.iloc[-1])
    both, _ = scores.directed_z(pd.Series([1.0, 2.0, 3.0, 4.0, 500.0, -500.0]), "both",
                                pd.Series(True, index=range(6)), 5.0)
    assert (both >= 0).all() and both.max() == 5.0


def test_family_is_mean_of_slots():
    a, b = np.linspace(0, 1, 21), np.linspace(5, 6, 21)
    out = scores.compute(_table(a=a, b=b), _cfg(**ONE_FAMILY)).scores
    ref = pd.Series(True, index=range(21))
    za, _ = scores.directed_z(pd.Series(a), "high", ref, 5.0)
    zb, _ = scores.directed_z(pd.Series(b), "low", ref, 5.0)
    np.testing.assert_allclose(out["qc_f"], ((za + zb) / 2).round(3), atol=1e-9)


def test_missing_slots_never_imputed():
    a = np.linspace(0, 1, 21)
    b = np.linspace(5, 6, 21)
    b[0] = np.nan
    a[1] = np.nan
    b[1] = np.nan
    cfg = _cfg(scoring=dict(min_slots=2, min_families=1, core_families=[],
                            families=ONE_FAMILY["scoring"]["families"]))
    out = scores.compute(_table(a=a, b=b), cfg).scores
    assert pd.isna(out["qc_f"].iloc[0])  # one of two slots, below the required two
    assert pd.isna(out["qc_f"].iloc[1])
    assert out["cov_f"].iloc[0] == "1/2"


def test_overall_requires_core_families():
    cfg = _cfg(scoring=dict(min_slots=1, min_families=1, core_families=["f"], families={
        "f": {"slots": [dict(name="a", metric="a")]},
        "g": {"slots": [dict(name="b", metric="b")]}}))
    a = np.linspace(0, 1, 21)
    a[0] = np.nan
    out = scores.compute(_table(a=a, b=np.linspace(0, 1, 21)), cfg).scores
    assert pd.isna(out["qc_overall"].iloc[0])
    assert out["overall_is_complete"].iloc[0] == 0
    assert out["missing_families"].iloc[0] == "f"
    assert out["overall_is_complete"].iloc[1] == 1


SUB = dict(scoring=dict(min_slots=1, min_families=1, core_families=[], families={
    "f": {"slots": [dict(name="a", metric="p", substitute=dict(metric="s", kind="k"))]}}))


def test_substitutes_ignored_unless_fallback_enabled():
    p = np.linspace(0, 1, 21)
    p[3] = np.nan
    out = scores.compute(_table(p=p, s=np.linspace(0, 100, 21)), _cfg(**SUB))
    assert pd.isna(out.scores["qc_f"].iloc[3])
    assert out.scores["n_substituted"].sum() == 0


def test_substitute_scaled_on_own_distribution_and_logged():
    p = np.linspace(0, 1, 21)
    p[3] = np.nan
    s = np.linspace(0, 100, 21)
    out = scores.compute(_table(p=p, s=s), _cfg(fallback=dict(enabled=True), **SUB))
    ref = pd.Series(True, index=range(21))
    zs, _ = scores.directed_z(pd.Series(s), "high", ref, 5.0)
    assert out.scores["qc_f"].iloc[3] == pytest.approx(round(zs.iloc[3], 3))
    assert out.scores["n_substituted"].iloc[3] == 1
    assert "s [k]" in out.scores["metric_source"].iloc[3]
    assert out.scores["metric_source"].iloc[0] == "all primary"
    row = out.sources[out.sources.participant_id == "sub-003"].iloc[0]
    assert row.metric_source == "s [k]"
    assert out.slots.iloc[0].rho_primary_substitute == pytest.approx(1.0)


def test_substitute_never_overrides_a_present_primary():
    p = np.linspace(0, 1, 21)
    out = scores.compute(_table(p=p, s=np.linspace(0, 100, 21)),
                         _cfg(fallback=dict(enabled=True), **SUB))
    assert out.scores["n_substituted"].sum() == 0


def test_effective_dimension():
    a = pd.Series(np.arange(30.0))
    assert scores.effective_dimension(pd.DataFrame({"a": a, "b": a * 2})) == pytest.approx(1.0)
    assert scores.effective_dimension(pd.DataFrame({"a": a})) == 1.0
