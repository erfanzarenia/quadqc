"""Read FSL eddy_quad outputs (qc.json, eddy_msr.txt) into a flat metric dictionary.

Field semantics follow the eddy_qc source. Three points matter for interpretation:

* eddy_quad writes -1 sentinels when an eddy output was absent. They are read as
  missing, never as values.
* qc_params_avg[0:6] is the mean of the SIGNED movement parameters, so the norms
  below describe a mean offset from the reference volume and not RMS motion.
* qc_params_avg[6:9] is the standard deviation of the linear eddy-current terms.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

import numpy as np

CORE_FIELDS = ("qc_mot_abs", "qc_mot_rel", "qc_outliers_tot", "qc_cnr_avg")
B0_TOLERANCE = 100.0  # eddy_quad treats |b| <= 100 as b=0


class QuadError(ValueError):
    """A qc.json file cannot be used at all."""


def load_qc_json(path: Path) -> dict:
    try:
        with open(path) as handle:
            data = json.load(handle)
    except OSError as exc:
        raise QuadError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise QuadError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise QuadError(f"{path} does not contain a JSON object")
    if not any(key in data for key in CORE_FIELDS):
        raise QuadError(f"{path} has none of the expected eddy_quad fields")
    return data


def _round(value, digits: int) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return round(value, digits) if np.isfinite(value) else float("nan")


def _scalar(qc: dict, key: str, digits: int) -> float:
    value = _round(qc.get(key), digits)
    return float("nan") if value == -1 else value


def _vector(qc: dict, key: str, flag: Optional[str] = None) -> list[float]:
    """Numeric list for `key`, or [] when eddy_quad recorded it as unavailable."""
    if flag is not None and qc.get(flag) is False:
        return []
    raw = qc.get(key)
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    try:
        values = [float(v) for v in raw]
    except (TypeError, ValueError):
        return []
    return [] if all(v == -1 for v in values) else values


def assign_shells(bvals: np.ndarray, shells: Optional[Sequence[float]] = None) -> np.ndarray:
    """Nominal shell per volume: 0 within tolerance, else the nearest given shell.

    Without `shells`, b-values are rounded to the nearest 100 s/mm^2.
    """
    bvals = np.asarray(bvals, float)
    if shells:
        ref = np.asarray(sorted(shells), float)
        out = ref[np.argmin(np.abs(bvals[:, None] - ref[None, :]), axis=1)]
    else:
        out = np.round(bvals / 100.0) * 100.0
    out[np.abs(bvals) <= B0_TOLERANCE] = 0.0
    return out


def msr_metrics(msr: np.ndarray, shells: Optional[np.ndarray]) -> tuple[dict, list[str]]:
    """Summaries of eddy's per-volume mean squared residuals.

    b0 volumes carry systematically larger residuals, so each volume is scored
    against its own shell. Without per-volume shells the within-shell metrics stay
    missing, because a pooled score would mostly detect b0 volumes.
    """
    out: dict = {"msr_median": _round(np.median(msr), 3), "msr_max": _round(msr.max(), 3)}
    notes = []
    if shells is None:
        notes.append("no per-volume b-values: within-shell MSR scores unavailable")
        return out, notes
    if shells.size != msr.size:
        notes.append(f"b-values ({shells.size}) do not match eddy_msr.txt ({msr.size})")
        return out, notes
    z = np.full(msr.size, np.nan)
    for shell in np.unique(shells):
        idx = np.where(shells == shell)[0]
        if idx.size >= 4:
            median = np.median(msr[idx])
            mad = np.median(np.abs(msr[idx] - median)) * 1.4826
            z[idx] = (msr[idx] - median) / max(mad, 1e-9)
    if np.isfinite(z).any():
        out["msr_max_z_within_shell"] = _round(np.nanmax(z), 2)
        out["msr_n_vol_z_gt5"] = int(np.nansum(z > 5))
    return out, notes


def read_bvals(path: Path) -> np.ndarray:
    return np.loadtxt(path, ndmin=1)


def extract(qc: dict, msr_path: Optional[Path] = None,
            bvals: Optional[np.ndarray] = None) -> tuple[dict, list[str]]:
    """Return (metrics, notes) for one qc.json. Unavailable metrics are simply absent."""
    res: dict = {}
    notes: list[str] = []
    shells = [float(b) for b in _vector(qc, "data_unique_bvals")]

    for key, name in (("qc_mot_abs", "eddy_motion_abs_mm"), ("qc_mot_rel", "eddy_motion_rel_mm")):
        value = _scalar(qc, key, 3)
        if np.isfinite(value):
            res[name] = value

    if qc.get("qc_ol_flag") is not False:
        total = _scalar(qc, "qc_outliers_tot", 3)
        if np.isfinite(total):
            res["eddy_outliers_pct_tot"] = total
        for b, value in zip(shells, _vector(qc, "qc_outliers_b")):
            res[f"eddy_outliers_pct_b{int(b)}"] = _round(value, 3)
        for i, value in enumerate(_vector(qc, "qc_outliers_pe"), start=1):
            res[f"eddy_outliers_pct_pe{i}"] = _round(value, 3)

    # qc_cnr_avg is [b0 SNR, CNR of each shell in data_unique_bvals order].
    cnr = _vector(qc, "qc_cnr_avg", "qc_cnr_flag")
    if cnr:
        res["eddy_snr_b0"] = _round(cnr[0], 3)
        for b, value in zip(shells, cnr[1:]):
            res[f"eddy_cnr_b{int(b)}"] = _round(value, 3)
        if shells and len(cnr) > 1:
            res["eddy_cnr_lowest_shell"] = res[f"eddy_cnr_b{int(shells[int(np.argmin(shells))])}"]
    cnr_std = _vector(qc, "qc_cnr_std", "qc_cnr_flag")
    if cnr_std:
        res["eddy_snr_b0_std"] = _round(cnr_std[0], 3)

    params = _vector(qc, "qc_params_avg", "qc_params_flag")
    if len(params) >= 6:
        res["eddy_trans_mean_norm_mm"] = _round(np.linalg.norm(params[:3]), 4)
        res["eddy_rot_mean_norm_deg"] = _round(np.degrees(np.linalg.norm(params[3:6])), 4)
    if len(params) >= 9:
        res["eddy_ec_linear_std_norm"] = _round(np.linalg.norm(params[6:9]), 5)

    s2v = _vector(qc, "qc_s2v_params_avg_std", "qc_s2v_params_flag")
    if len(s2v) >= 6:
        res["eddy_s2v_trans_std_norm_mm"] = _round(np.linalg.norm(s2v[:3]), 4)
        res["eddy_s2v_rot_std_norm_deg"] = _round(np.linalg.norm(s2v[3:6]), 4)
    field_std = _vector(qc, "qc_vox_displ_std", "qc_field_flag")
    if field_std:
        res["eddy_field_displ_std_vox"] = _round(field_std[0], 4)

    for key, name in (("data_no_b0_vols", "eddy_n_b0_vols"), ("data_no_dw_vols", "eddy_n_dw_vols"),
                      ("data_no_PE_dirs", "eddy_n_pe_dirs")):
        value = _round(qc.get(key), 0)
        if np.isfinite(value):
            res[name] = int(value)

    vox = _vector(qc, "data_vox_size")
    if vox:
        res["eddy_vox_mm"] = _round(np.mean(vox[:3]), 4)

    if msr_path is not None and Path(msr_path).is_file() and qc.get("qc_rss_flag") is not False:
        try:
            msr = np.loadtxt(msr_path, ndmin=1).ravel()
        except ValueError:
            notes.append("eddy_msr.txt could not be parsed")
        else:
            if msr.size and np.all(msr >= 0):
                vol_shells = None if bvals is None else assign_shells(bvals, shells or None)
                extra, msr_notes = msr_metrics(msr, vol_shells)
                res.update(extra)
                notes.extend(msr_notes)
    return res, notes


def state_of(metrics: dict) -> str:
    """complete, or partial when a core eddy_quad measurement is unavailable."""
    core = ("eddy_motion_abs_mm", "eddy_motion_rel_mm", "eddy_outliers_pct_tot", "eddy_snr_b0")
    return "complete" if all(name in metrics for name in core) else "partial"
