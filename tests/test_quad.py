import json

import numpy as np
import pytest
from conftest import make_qc

from quadqc import quad


def test_core_metrics_follow_quad_field_semantics():
    metrics, _ = quad.extract(make_qc())
    assert metrics["eddy_motion_abs_mm"] == 0.9
    assert metrics["eddy_motion_rel_mm"] == 0.4
    assert metrics["eddy_outliers_pct_tot"] == 0.5
    assert metrics["eddy_outliers_pct_b1000"] == 0.8
    assert metrics["eddy_outliers_pct_pe2"] == 0.5
    assert metrics["eddy_snr_b0"] == 20.0
    assert metrics["eddy_cnr_b1000"] == 1.6
    assert metrics["eddy_cnr_b2000"] == 2.4
    assert metrics["eddy_cnr_lowest_shell"] == 1.6
    assert metrics["eddy_n_pe_dirs"] == 2
    assert metrics["eddy_vox_mm"] == 2.0


def test_parameter_norms():
    metrics, _ = quad.extract(make_qc())
    assert metrics["eddy_trans_mean_norm_mm"] == pytest.approx(0.05)  # 3-4-5 triangle
    assert metrics["eddy_rot_mean_norm_deg"] == pytest.approx(np.degrees(0.01), abs=1e-4)
    assert metrics["eddy_ec_linear_std_norm"] == pytest.approx(0.005)


def test_lowest_shell_does_not_assume_order_or_values():
    qc = make_qc(data_unique_bvals=[3000, 700], qc_cnr_avg=[20.0, 2.5, 1.1])
    metrics, _ = quad.extract(qc)
    assert metrics["eddy_cnr_b700"] == 1.1
    assert metrics["eddy_cnr_lowest_shell"] == 1.1


@pytest.mark.parametrize("field,flag,absent", [
    ("qc_cnr_avg", "qc_cnr_flag", ["eddy_snr_b0", "eddy_cnr_b1000"]),
    ("qc_params_avg", "qc_params_flag", ["eddy_rot_mean_norm_deg", "eddy_ec_linear_std_norm"]),
    ("qc_outliers_tot", "qc_ol_flag", ["eddy_outliers_pct_tot"]),
])
def test_sentinels_are_missing_not_values(field, flag, absent):
    qc = make_qc(**{flag: False, field: -1.0 if field == "qc_outliers_tot" else [-1.0] * 9})
    metrics, _ = quad.extract(qc)
    for name in absent:
        assert name not in metrics
    assert quad.state_of(metrics) == "partial" or field == "qc_params_avg"


def test_sentinels_without_flags_from_older_versions():
    qc = make_qc(qc_mot_abs=-1.0, qc_cnr_avg=[-1.0, -1.0, -1.0])
    del qc["qc_cnr_flag"]
    metrics, _ = quad.extract(qc)
    assert "eddy_motion_abs_mm" not in metrics and "eddy_snr_b0" not in metrics


def test_negative_signed_parameters_are_kept():
    qc = make_qc(qc_params_avg=[-1.0, -0.5, 0.2, 0.01, 0.0, 0.0, 0.1, 0.0, 0.0])
    metrics, _ = quad.extract(qc)
    assert metrics["eddy_trans_mean_norm_mm"] > 1


def test_msr_within_shell_scored_per_shell(tmp_path):
    # Eight b0 at residual ~50 and eight DW volumes at ~3, with one DW outlier.
    msr = np.array([50, 51, 49, 52, 48, 50, 51, 49, 3, 3.1, 2.9, 3.2, 2.8, 3, 3.1, 30.0])
    bvals = np.array([0] * 8 + [1000] * 8, float)
    path = tmp_path / "eddy_msr.txt"
    np.savetxt(path, msr.reshape(1, -1))
    metrics, notes = quad.extract(make_qc(data_unique_bvals=[1000]), path, bvals)
    dw = msr[8:]
    mad = np.median(np.abs(dw - np.median(dw))) * 1.4826
    assert metrics["msr_max_z_within_shell"] == pytest.approx((30 - np.median(dw)) / mad, abs=0.01)
    assert metrics["msr_n_vol_z_gt5"] == 1
    assert not notes


def test_msr_without_bvals_leaves_within_shell_missing(tmp_path):
    path = tmp_path / "eddy_msr.txt"
    np.savetxt(path, np.ones((1, 10)))
    metrics, notes = quad.extract(make_qc(), path, None)
    assert "msr_max_z_within_shell" not in metrics
    assert "msr_median" in metrics
    assert any("within-shell" in n for n in notes)


def test_msr_length_mismatch_is_reported(tmp_path):
    path = tmp_path / "eddy_msr.txt"
    np.savetxt(path, np.ones((1, 10)))
    metrics, notes = quad.extract(make_qc(), path, np.zeros(9))
    assert "msr_max_z_within_shell" not in metrics
    assert any("do not match" in n for n in notes)


@pytest.mark.parametrize("text", ["{ nope", "[1, 2]", json.dumps({"unrelated": 1})])
def test_unusable_json_raises(tmp_path, text):
    path = tmp_path / "qc.json"
    path.write_text(text)
    with pytest.raises(quad.QuadError):
        quad.load_qc_json(path)


def test_assign_shells():
    bvals = np.array([5, 95, 990, 1010, 1995])
    assert list(quad.assign_shells(bvals, [1000, 2000])) == [0, 0, 1000, 1000, 2000]
    assert list(quad.assign_shells(np.array([0, 695, 1005]))) == [0, 700, 1000]
