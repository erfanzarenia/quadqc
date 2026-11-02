"""Shared synthetic eddy_quad fixtures. No real participant data is used anywhere."""

import json
from pathlib import Path

import numpy as np
import pytest


def make_qc(**over):
    """A qc.json dictionary with the structure written by eddy_quad."""
    qc = {
        "data_no_PE_dirs": 2,
        "data_no_b0_vols": 4,
        "data_no_dw_vols": 12,
        "data_no_shells": 2,
        "data_unique_bvals": [1000, 2000],
        "data_unique_pes": [1, 2],
        "data_vox_size": [2.0, 2.0, 2.0],
        "data_file_eddy": "work/dwi.nii.gz",
        "data_file_bvals": "work/dwi.bval",
        "qc_mot_abs": 0.9,
        "qc_mot_rel": 0.4,
        "qc_ol_flag": True,
        "qc_outliers_tot": 0.5,
        "qc_outliers_b": [0.8, 0.2],
        "qc_outliers_pe": [0.0, 0.5],
        "qc_cnr_flag": True,
        "qc_cnr_avg": [20.0, 1.6, 2.4],
        "qc_cnr_std": [10.0, 1.0, 1.2],
        "qc_params_flag": True,
        "qc_params_avg": [0.03, -0.04, 0.0, 0.01, 0.0, 0.0, 0.003, 0.004, 0.0],
        "qc_rss_flag": True,
        "qc_s2v_params_flag": False,
        "qc_s2v_params_avg_std": [-1.0] * 6,
        "qc_field_flag": False,
        "qc_vox_displ_std": [-1.0],
    }
    qc.update(over)
    return qc


def write_quad(root: Path, relative: str, qc=None, msr=None) -> Path:
    directory = root / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "qc.json").write_text(json.dumps(make_qc() if qc is None else qc))
    if msr is not None:
        np.savetxt(directory / "eddy_msr.txt", np.reshape(msr, (1, -1)), fmt="%f")
    return directory


@pytest.fixture
def quad_tree(tmp_path):
    """Twenty-four complete participants plus one of each failure mode."""
    root = tmp_path / "derivatives"
    rng = np.random.default_rng(7)
    for i in range(1, 25):
        qc = make_qc(qc_mot_abs=round(float(rng.uniform(0.5, 1.2)), 2),
                     qc_mot_rel=round(float(rng.uniform(0.3, 0.6)), 2),
                     qc_outliers_tot=float(rng.uniform(0.1, 1.0)),
                     qc_cnr_avg=[float(rng.uniform(15, 25)), float(rng.uniform(1.4, 1.8)),
                                 float(rng.uniform(2.2, 2.8))])
        write_quad(root, f"sub-{i:02d}/qc/sub-{i:02d}_eddyqc", qc)
    write_quad(root, "sub-90/qc/sub-90_eddyqc", make_qc(qc_mot_abs=5.0))  # severe motion
    stub = root / "sub-91/qc/sub-91_eddyqc"  # eddy_quad crashed: a report stub, no qc.json
    stub.mkdir(parents=True)
    (stub / "qc.pdf").write_text("eddy_quad FAILED")
    corrupt = root / "sub-92/qc/sub-92_eddyqc"
    corrupt.mkdir(parents=True)
    (corrupt / "qc.json").write_text("{ not json")
    (root / "sub-93").mkdir()  # participant directory, no QUAD output at all
    write_quad(root, "sub-94/qc/sub-94_eddyqc",  # partial: outlier map was absent
               make_qc(qc_ol_flag=False, qc_outliers_tot=-1.0))
    participants = tmp_path / "participants.tsv"
    ids = [f"sub-{i:02d}" for i in range(1, 25)] + [f"sub-{i}" for i in range(90, 96)]
    participants.write_text("participant_id\tage\n" + "".join(f"{p}\t30\n" for p in ids))
    return root, participants
