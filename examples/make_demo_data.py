#!/usr/bin/env python3
"""Create a small synthetic eddy_quad tree for trying quadqc. Contains no real data.

    python make_demo_data.py demo_data
"""

import json
import sys
from pathlib import Path

import numpy as np


def qc_json(rng, **over):
    qc = {
        "data_no_PE_dirs": 2, "data_no_b0_vols": 6, "data_no_dw_vols": 60,
        "data_unique_bvals": [1000, 2000], "data_unique_pes": [1, 2],
        "data_vox_size": [2.0, 2.0, 2.0],
        "qc_mot_abs": round(float(rng.uniform(0.5, 1.5)), 2),
        "qc_mot_rel": round(float(rng.uniform(0.3, 0.8)), 2),
        "qc_ol_flag": True, "qc_outliers_tot": float(rng.uniform(0.1, 1.2)),
        "qc_outliers_b": [float(rng.uniform(0.1, 2)), float(rng.uniform(0, 0.5))],
        "qc_outliers_pe": [0.0, float(rng.uniform(0.1, 1))],
        "qc_cnr_flag": True,
        "qc_cnr_avg": [float(rng.uniform(15, 30)), float(rng.uniform(1.4, 2.2)),
                       float(rng.uniform(2, 3))],
        "qc_cnr_std": [float(rng.uniform(5, 10)), 1.0, 1.2],
        "qc_params_flag": True,
        "qc_params_avg": [float(v) for v in rng.normal(0, 0.1, 3)]
        + [float(v) for v in rng.normal(0, 0.005, 3)]
        + [float(v) for v in rng.uniform(0.02, 0.1, 3)],
        "qc_rss_flag": True, "qc_s2v_params_flag": False,
        "qc_s2v_params_avg_std": [-1.0] * 6, "qc_field_flag": False,
        "qc_vox_displ_std": [-1.0],
    }
    qc.update(over)
    return qc


def write(root, label, qc):
    directory = root / label / "dwi" / f"{label}_eddy.qc"
    directory.mkdir(parents=True)
    (directory / "qc.json").write_text(json.dumps(qc, indent=1))
    return directory


def main(destination):
    base = Path(destination)
    if base.exists():
        sys.exit(f"{base} already exists")
    root = base / "derivatives"
    rng = np.random.default_rng(2026)
    for i in range(1, 25):
        write(root, f"sub-{i:02d}", qc_json(rng))
    write(root, "sub-25", qc_json(rng, qc_mot_abs=5.2))  # fail: severe absolute motion
    write(root, "sub-26", qc_json(rng, qc_mot_abs=2.4))  # caveat: review-level motion
    write(root, "sub-27", qc_json(rng, qc_ol_flag=False, qc_outliers_tot=-1.0))  # incomplete
    write(root, "sub-28", qc_json(rng, qc_mot_abs=2.4, qc_outliers_tot=2.5,
                                  qc_cnr_avg=[9.0, 1.6, 2.4]))  # fail: three domains converge
    stub = root / "sub-29" / "dwi" / "sub-29_eddy.qc"  # eddy_quad crashed
    stub.mkdir(parents=True)
    (stub / "qc.pdf").write_text("eddy_quad failed\n")
    bad = root / "sub-30" / "dwi" / "sub-30_eddy.qc"  # truncated file
    bad.mkdir(parents=True)
    (bad / "qc.json").write_text('{"qc_mot_abs": 0.')
    ids = [f"sub-{i:02d}" for i in range(1, 32)]  # sub-31 was scanned, nothing was processed
    (base / "participants.tsv").write_text(
        "participant_id\n" + "".join(f"{p}\n" for p in ids))
    print(f"Created {base}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
