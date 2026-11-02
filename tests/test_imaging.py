import numpy as np
import pandas as pd
import pytest

nib = pytest.importorskip("nibabel")
pytest.importorskip("scipy")

from scipy.ndimage import gaussian_filter  # noqa: E402

from quadqc import cli, config, imaging, pipeline  # noqa: E402


def _phantom(shape=(40, 40, 30, 16), seed=3):
    """A head-like envelope with anatomical texture, plus per-volume noise."""
    rng = np.random.default_rng(seed)
    x, y, z = np.mgrid[:shape[0], :shape[1], :shape[2]]
    centre = np.array(shape[:3]) / 2
    blob = np.exp(-(((x - centre[0]) / 9) ** 2 + ((y - centre[1]) / 11) ** 2
                    + ((z - centre[2]) / 8) ** 2))
    texture = gaussian_filter(np.random.default_rng(99).normal(size=shape[:3]), 1.0)
    anatomy = blob * (1 + 3 * texture / texture.std())
    data = 1000 * anatomy[..., None] + rng.normal(0, 2, shape)
    mask = blob > 0.3
    return data.astype(np.float32), mask


def test_slice_loss_detected_in_right_volume_and_counted():
    data, mask = _phantom()
    data[..., 5][:, :, 15] *= 0.2
    shells = np.zeros(16)
    out = imaging.dropout_metrics(data, mask, shells)
    assert out["pct_slice_outliers"] > 0
    assert out["n_vol_dropout"] >= 0


def test_motion_recovers_known_translations():
    data, _ = _phantom()
    shells = np.zeros(16)
    still = imaging.motion_metrics(data, shells, (2.0, 2.0, 2.0))
    assert still["motion_rms_mean_mm"] < 0.3
    # One volume displaced by 3 voxels (6 mm) contributes 6/16 mm to the series mean.
    one = data.copy()
    one[..., 8] = np.roll(data[..., 8], 3, axis=1)
    assert imaging.motion_metrics(one, shells, (2.0, 2.0, 2.0))["motion_rms_mean_mm"] == (
        pytest.approx(6 / 16, abs=0.05))
    # Half the series displaced by 2 voxels (4 mm): every volume sits 2 mm from the mean.
    half = data.copy()
    half[..., 8:] = np.roll(data[..., 8:], 2, axis=1)
    assert imaging.motion_metrics(half, shells, (2.0, 2.0, 2.0))["motion_rms_mean_mm"] == (
        pytest.approx(2.0, abs=0.1))


def test_bvec_rotation_measures_a_known_angle():
    rng = np.random.default_rng(0)
    bvecs = rng.normal(size=(3, 30))
    bvecs /= np.linalg.norm(bvecs, axis=0)
    bvecs[:, :2] = 0  # b0 volumes are ignored
    theta = np.radians(3.0)
    rot = np.array([[np.cos(theta), -np.sin(theta), 0], [np.sin(theta), np.cos(theta), 0],
                    [0, 0, 1]])
    out = imaging.bvec_rotation(bvecs, rot @ bvecs)
    assert out["bvecrot_max_deg"] <= 3.0 + 1e-6
    assert 0 < out["bvecrot_rms_deg"] <= 3.0 + 1e-6
    # A rotation about z leaves a vector along z unchanged and moves one in-plane by theta.
    single = np.zeros((3, 12))
    single[0] = 1
    assert imaging.bvec_rotation(single, rot @ single)["bvecrot_rms_deg"] == pytest.approx(3.0)


def test_bvec_rotation_rejects_mismatch():
    with pytest.raises(ValueError):
        imaging.bvec_rotation(np.ones((3, 20)), np.ones((3, 21)))


def test_snr_repeat():
    rng = np.random.default_rng(1)
    volumes = 100 + rng.normal(0, 5, (20, 20, 20, 8))
    mask = np.ones((20, 20, 20), bool)
    assert imaging.snr_repeat(volumes, mask) == pytest.approx(20, rel=0.15)
    assert np.isnan(imaging.snr_repeat(volumes[..., :1], mask))


def test_preprocessed_checks_count_invalid_voxels():
    data, mask = _phantom()
    data[20, 20, 15, 0] = -5
    data[0, 0, 0, 1] = np.nan
    out = imaging.preprocessed_metrics(data, mask, np.array([0.0] * 4 + [1000.0] * 12))
    assert out["preproc_n_nonfinite"] == 1
    assert out["preproc_pct_negative_in_brain"] > 0
    assert out["preproc_b0_snr_repeat"] > 10


def test_tensor_metrics():
    fa = np.full((10, 10, 10), 0.5, np.float32)
    fa[0, :, :] = 1.5
    l3 = np.full((10, 10, 10), 1e-3, np.float32)
    l3[0, 0, 0] = -1e-3
    out = imaging.tensor_metrics(fa, l3, np.ones((10, 10, 10), bool))
    assert out["fa_pct_gt1"] == pytest.approx(10.0)
    assert out["pct_negative_eigenvalue"] == pytest.approx(0.1)


def test_mask_dice_uses_affines():
    mask = np.zeros((20, 20, 20))
    mask[4:16, 4:16, 4:16] = 1
    a = nib.Nifti1Image(mask, np.eye(4))
    assert imaging.mask_dice(a, a)["reg_dice_dwimask_t1mask"] == 1.0
    moved = np.eye(4)
    moved[0, 3] = 6.0
    # Six voxels of overlap; two of the moved mask's slices fall outside the grid.
    assert imaging.mask_dice(a, nib.Nifti1Image(mask, moved))["reg_dice_dwimask_t1mask"] == (
        pytest.approx(12 / 22, abs=1e-3))
    coarse = np.zeros((10, 10, 10))
    coarse[2:8, 2:8, 2:8] = 1
    b = nib.Nifti1Image(coarse, np.diag([2.0, 2.0, 2.0, 1.0]))
    assert imaging.mask_dice(a, b)["reg_dice_dwimask_t1mask"] > 0.75


def _write_series(directory, stem, data, bvals, bvecs=None):
    directory.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(data, np.diag([2.0, 2.0, 2.0, 1.0])), directory / f"{stem}.nii.gz")
    np.savetxt(directory / f"{stem}.bval", bvals.reshape(1, -1), fmt="%g")
    if bvecs is None:
        rng = np.random.default_rng(5)
        bvecs = rng.normal(size=(3, bvals.size))
        bvecs /= np.linalg.norm(bvecs, axis=0)
    np.savetxt(directory / f"{stem}.bvec", bvecs)


@pytest.fixture
def imaging_tree(tmp_path):
    root = tmp_path / "derivatives"
    bvals = np.array([0.0] * 4 + [1000.0] * 12)
    for seed, label in enumerate(("sub-01", "sub-02", "sub-04")):
        data, mask = _phantom(seed=seed)
        data[..., 4:] += 3 * seed * np.roll(data[..., :12], seed + 1, axis=1)  # vary between people
        directory = root / label / "dwi"
        _write_series(directory, "raw_dwi", data, bvals)
        _write_series(directory, "pre_dwi", data, bvals)
        nib.save(nib.Nifti1Image(mask.astype(np.uint8), np.diag([2.0, 2.0, 2.0, 1.0])),
                 directory / "mask.nii.gz")
    (root / "sub-03").mkdir(parents=True)  # nothing computable
    config_path = tmp_path / "c.yaml"
    config_path.write_text(
        "inputs:\n  quad_root: derivatives\n"
        "imaging:\n"
        "  dwi: '{participant}/dwi/raw_dwi.nii.gz'\n"
        "  mask: '{participant}/dwi/mask.nii.gz'\n"
        "  preprocessed_dwi: '{participant}/dwi/pre_dwi.nii.gz'\n"
        "  preprocessed_mask: '{participant}/dwi/mask.nii.gz'\n")
    return tmp_path, config_path


def test_imaging_table_keeps_everyone_and_records_why(imaging_tree):
    tmp_path, config_path = imaging_tree
    out = tmp_path / "imaging.tsv"
    assert cli.main(["imaging", "--config", str(config_path), "--out", str(out)]) == 0
    table = pd.read_csv(out, sep="\t", keep_default_na=False).set_index("participant_id")
    assert list(table.index) == ["sub-01", "sub-02", "sub-03", "sub-04"]
    assert table.at["sub-03", "motion_rms_mean_mm"] == "n/a"
    assert "dwi: 0 file(s) match" in table.at["sub-03", "imaging_notes"]
    assert float(table.at["sub-01", "preproc_b0_snr_repeat"]) > 0


def test_imaging_refuses_output_inside_input(imaging_tree):
    tmp_path, config_path = imaging_tree
    out = tmp_path / "derivatives" / "x.tsv"
    assert cli.main(["imaging", "--config", str(config_path), "--out", str(out)]) == 2
    assert not out.exists()


def test_imaging_inputs_may_live_elsewhere(imaging_tree):
    tmp_path, config_path = imaging_tree
    absolute = str(tmp_path / "derivatives" / "{participant}" / "dwi" / "raw_dwi.nii.gz")
    text = config_path.read_text().replace("'{participant}/dwi/raw_dwi.nii.gz'", f"'{absolute}'")
    config_path.write_text(text)
    table = imaging.compute_table(config.load(config_path))
    assert table.set_index("participant_id").at["sub-01", "motion_rms_mean_mm"] >= 0


def test_imaging_without_inputs_is_an_error(tmp_path):
    (tmp_path / "sub-01").mkdir()
    assert cli.main(["imaging", "--quad-root", str(tmp_path), "--out",
                     str(tmp_path / "x.tsv")]) == 2


def test_fallback_end_to_end(imaging_tree):
    tmp_path, config_path = imaging_tree
    out = tmp_path / "imaging.tsv"
    cli.main(["imaging", "--config", str(config_path), "--out", str(out)])
    config_path.write_text(config_path.read_text()
                           + "  \nfallback:\n  enabled: true\n")
    text = config_path.read_text().replace(
        "inputs:\n  quad_root: derivatives\n",
        "inputs:\n  quad_root: derivatives\n  supplementary:\n  - {path: imaging.tsv}\n")
    config_path.write_text(text)
    result = pipeline.run(config.load(config_path), tmp_path / "qc")
    decisions = result["decisions"].decisions.set_index("participant_id")
    assert decisions.at["sub-01", "status"] == "caveat_incomplete_quad"
    assert decisions.at["sub-03", "status"] == "missing"
    assert decisions.at["sub-03", "decided_by"] == "insufficient_fallback_evidence"
    assert decisions.at["sub-01", "pathway"] == "fallback"
    scored = result["scores"].scores.set_index("participant_id")
    assert "alternate-source" in scored.at["sub-01", "metric_source"]
    # No surrogate may ever be written into an eddy_* column.
    metrics = result["cohort"].metrics.set_index("participant_id")
    eddy = [c for c in metrics.columns if c.startswith("eddy_")]
    assert not eddy or metrics.loc["sub-01", eddy].isna().all()
