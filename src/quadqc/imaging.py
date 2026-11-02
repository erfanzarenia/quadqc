"""Optional NIfTI-based metrics, written as a supplementary table for `quadqc run`.

Two uses: plausibility checks on preprocessed outputs, and the surrogates required by
the optional fallback pathway for participants without eddy_quad metrics. Nothing here
estimates an eddy_quad value. Every input group is optional; what cannot be computed
stays missing and the reason is recorded in `imaging_notes`.

Assumptions: axial acquisition (slices along the third axis), .bval/.bvec sidecars
beside each DWI file, and a brain mask supplied by the user. No mask is ever derived
here, because a poor automatic mask silently corrupts every metric below.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

import nibabel as nib
import numpy as np
import pandas as pd
from scipy import ndimage as ndi

from . import discovery
from .config import Config, ConfigError
from .quad import assign_shells
from .tables import write_tsv

_WHITEN = 0.5  # partial whitening: robust peak localisation, good sub-voxel accuracy
_AXES = (0, 1, 2)


def _round(value, digits: int) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return round(value, digits) if np.isfinite(value) else float("nan")


def _sidecar(path: Path, suffix: str) -> Path:
    name = path.name
    stem = name[:-len(".nii.gz")] if name.endswith(".nii.gz") else path.stem
    return path.with_name(stem + suffix)


def _load_gradients(path: Path, nvol: int) -> tuple[np.ndarray, np.ndarray]:
    bvals = np.loadtxt(_sidecar(path, ".bval"), ndmin=1).ravel()
    bvecs = np.loadtxt(_sidecar(path, ".bvec"), ndmin=2)
    if bvecs.shape[0] != 3:
        bvecs = bvecs.T
    if bvals.size != nvol or bvecs.shape[1] != nvol:
        raise ValueError(f"{path.name}: {nvol} volumes, {bvals.size} b-values, "
                         f"{bvecs.shape[1]} b-vectors")
    return bvals, bvecs


# ---------------------------------------------------------------------------
# Slice-wise signal loss (after Oguz et al. 2014, DTIPrep)
# ---------------------------------------------------------------------------


def _robust_z(values: np.ndarray, axis: int = 0) -> np.ndarray:
    median = np.median(values, axis=axis, keepdims=True)
    mad = np.median(np.abs(values - median), axis=axis, keepdims=True) * 1.4826
    mad = np.where(mad < 1e-9, np.nan, mad)
    return (values - median) / mad


def dropout_metrics(data: np.ndarray, mask: np.ndarray, shells: np.ndarray) -> dict:
    """Within-shell slice and volume signal-loss counts."""
    nz, nv = data.shape[2], data.shape[3]
    counts = mask.sum(axis=(0, 1)).astype(np.float64)
    total = mask.sum()
    profile = np.full((nv, nz), np.nan)
    vmean = np.zeros(nv)
    for v in range(nv):
        summed = (data[..., v] * mask).sum(axis=(0, 1), dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            profile[v] = np.where(counts > 50, summed / np.maximum(counts, 1), np.nan)
        vmean[v] = summed.sum() / max(total, 1)

    slice_z = np.full((nv, nz), np.nan)
    vol_z = np.full(nv, np.nan)
    for shell in np.unique(shells):
        idx = np.where(shells == shell)[0]
        if idx.size < 4:
            continue
        relative = profile[idx] / np.maximum(vmean[idx][:, None], 1e-9)
        slice_z[idx] = _robust_z(relative, axis=0)
        vol_z[idx] = _robust_z(vmean[idx][:, None], axis=0)[:, 0]

    valid = np.isfinite(slice_z)
    bad = valid & (slice_z < -3.0)
    finite_vol = np.isfinite(vol_z)
    dropped = int((finite_vol & (vol_z < -3)).sum())
    return {"pct_slice_outliers": round(100.0 * bad.sum() / max(valid.sum(), 1), 3),
            "n_vol_with_ge3_outlier_slices": int((bad.sum(1) >= 3).sum()),
            "n_vol_dropout": dropped, "pct_vol_dropout": round(100.0 * dropped / nv, 3)}


# ---------------------------------------------------------------------------
# Translation-only motion by FFT phase correlation
# ---------------------------------------------------------------------------


def _peak_offset(line: np.ndarray, index: int) -> float:
    """Sub-voxel refinement along one axis of the correlation surface."""
    n = line.size
    before, peak, after = line[(index - 1) % n], line[index], line[(index + 1) % n]
    curvature = before - 2 * peak + after
    if abs(curvature) < 1e-12:
        return 0.0
    return float(np.clip(0.5 * (before - after) / curvature, -1, 1))


def _phase_shift(ref_f: np.ndarray, moving: np.ndarray, shape: tuple) -> np.ndarray:
    """Displacement of `moving` relative to the reference, in voxels."""
    product = ref_f * np.conj(np.fft.rfftn(moving, axes=_AXES))
    product /= np.maximum(np.abs(product), 1e-9) ** _WHITEN
    surface = np.fft.irfftn(product, s=shape, axes=_AXES)
    peak = np.unravel_index(int(np.argmax(surface)), surface.shape)
    lines = (surface[:, peak[1], peak[2]], surface[peak[0], :, peak[2]],
             surface[peak[0], peak[1], :])
    shift = np.empty(3)
    for axis in range(3):
        value = peak[axis] + _peak_offset(lines[axis], peak[axis])
        if value > shape[axis] / 2:
            value -= shape[axis]
        shift[axis] = -value
    return shift


def motion_metrics(data: np.ndarray, shells: np.ndarray, zooms) -> dict:
    """Mean translation per volume against each shell's own mean volume.

    Two limits make this a relative screen and not a replacement for eddy's estimates:
    rotations are seen only through the translation they induce, and motion between
    shells is invisible because each shell has its own reference. On the development
    dataset these values were far smaller than eddy's: see docs/fallback.md.
    """
    shape = data.shape[:3]
    displacement = np.zeros((data.shape[3], 3))
    for shell in np.unique(shells):
        idx = np.where(shells == shell)[0]
        ref_f = np.fft.rfftn(data[..., idx].astype(np.float32).mean(3), axes=_AXES)
        for v in idx:
            displacement[v] = _phase_shift(ref_f, data[..., v].astype(np.float32), shape)
    mm = displacement * np.asarray(zooms[:3])[None, :]
    rms = np.sqrt((mm ** 2).sum(1))
    steps = np.sqrt((np.diff(mm, axis=0) ** 2).sum(1))
    return {"motion_rms_mean_mm": round(float(rms.mean()), 4),
            "motion_rel_mean_mm": round(float(steps.mean()), 4) if steps.size else np.nan}


# ---------------------------------------------------------------------------
# Rotation applied by eddy, measured from b-vectors
# ---------------------------------------------------------------------------


def bvec_rotation(before: np.ndarray, after: np.ndarray) -> dict:
    """Angle between the b-vectors given to eddy and those it rotated, per DW volume."""
    if before.shape != after.shape or before.ndim != 2:
        raise ValueError(f"b-vector shapes differ: {before.shape} and {after.shape}")
    if before.shape[0] != 3:
        before, after = before.T, after.T
    norm_a, norm_b = np.linalg.norm(before, axis=0), np.linalg.norm(after, axis=0)
    weighted = (norm_a > 1e-6) & (norm_b > 1e-6)
    if weighted.sum() < 10:
        raise ValueError("fewer than 10 diffusion-weighted b-vectors")
    cosine = (before[:, weighted] / norm_a[weighted] * after[:, weighted] / norm_b[weighted]).sum(0)
    angle = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
    return {"bvecrot_rms_deg": float(np.sqrt((angle ** 2).mean())),
            "bvecrot_max_deg": float(angle.max())}


# ---------------------------------------------------------------------------
# Preprocessed outputs
# ---------------------------------------------------------------------------


def snr_repeat(volumes: np.ndarray, mask: np.ndarray) -> float:
    """Median voxelwise mean/SD over repeated b0 volumes (Dietrich et al. 2007)."""
    if volumes.shape[-1] < 2 or mask.sum() < 500:
        return np.nan
    values = volumes[mask, :].astype(np.float32)
    mean, sd = values.mean(1), values.std(1, ddof=1)
    usable = (sd > 0) & (mean > 0)
    return float(np.median(mean[usable] / sd[usable])) if usable.sum() >= 200 else np.nan


def preprocessed_metrics(data: np.ndarray, mask: np.ndarray, bvals: np.ndarray) -> dict:
    res: dict = {}
    nonfinite = negative = 0
    for v in range(data.shape[3]):
        volume = data[..., v]
        nonfinite += int((~np.isfinite(volume)).sum())
        negative += int((volume[mask] < 0).sum())
    res["preproc_n_nonfinite"] = nonfinite
    res["preproc_pct_negative_in_brain"] = _round(
        100.0 * negative / max(int(mask.sum()) * data.shape[3], 1), 4)
    b0 = np.where(assign_shells(bvals) == 0)[0]
    if b0.size:
        volumes = np.stack([data[..., i].astype(np.float32) for i in b0], -1)
        res["preproc_b0_snr_repeat"] = _round(
            snr_repeat(volumes, ndi.binary_erosion(mask, iterations=2)), 2)
    return res


def tensor_metrics(fa: Optional[np.ndarray], l3: Optional[np.ndarray], mask: np.ndarray) -> dict:
    res: dict = {}
    if fa is not None:
        valid = mask & np.isfinite(fa)
        res["fa_pct_gt1"] = _round(100.0 * (fa[valid] > 1.0).mean(), 4)
    if l3 is not None:
        valid = mask & np.isfinite(l3)
        res["pct_negative_eigenvalue"] = _round(100.0 * (l3[valid] < 0).mean(), 4)
    return res


def mask_dice(dwi_img, t1_img) -> dict:
    """Dice between the DWI mask and a T1w mask resampled onto its grid."""
    matrix = np.linalg.inv(t1_img.affine).dot(dwi_img.affine)
    resampled = ndi.affine_transform(
        np.asanyarray(t1_img.dataobj).astype(np.float32), matrix[:3, :3], offset=matrix[:3, 3],
        output_shape=dwi_img.shape[:3], order=0, mode="constant", cval=0.0) > 0.5
    mask = dwi_img.get_fdata() > 0.5
    overlap = (resampled & mask).sum()
    dice = 2 * overlap / max(resampled.sum() + mask.sum(), 1)
    return {"reg_dice_dwimask_t1mask": _round(dice, 4)}


# ---------------------------------------------------------------------------
# Per-participant driver
# ---------------------------------------------------------------------------


def _resolve(pattern: Optional[str], root: Path, participant: str, session: str, notes: list,
             name: str) -> Optional[Path]:
    if not pattern:
        return None
    hits = discovery.match_template(pattern, root, participant, session)
    if len(hits) == 1:
        return hits[0]
    notes.append(f"{name}: {len(hits)} file(s) match")
    return None


def _one(job: tuple) -> dict:
    participant, session, root, patterns = job
    res: dict = {"participant_id": participant, "session_id": session}
    notes: list[str] = []

    def find(name):
        return _resolve(patterns.get(name), root, participant, session, notes, name)

    def attempt(label, function):
        try:
            res.update(function())
        except (ValueError, OSError, IndexError) as exc:
            notes.append(f"{label} failed: {exc}")

    dwi, mask = find("dwi"), find("mask")
    if dwi and mask:
        def raw_metrics():
            image = nib.load(dwi)
            data = np.asanyarray(image.dataobj)
            if data.ndim != 4:
                raise ValueError(f"{dwi.name} is not 4D")
            bvals, _ = _load_gradients(dwi, data.shape[3])
            brain = nib.load(mask).get_fdata() > 0.5
            if brain.shape != data.shape[:3]:
                raise ValueError("mask and DWI grids differ")
            shells = assign_shells(bvals)
            out = {"vox_mm": _round(np.mean(image.header.get_zooms()[:3]), 4)}
            out.update(dropout_metrics(data, brain, shells))
            out.update(motion_metrics(data, shells, image.header.get_zooms()))
            return out
        attempt("raw DWI metrics", raw_metrics)

    pre, pre_mask = find("preprocessed_dwi"), find("preprocessed_mask")
    if pre and pre_mask:
        def pre_metrics():
            image = nib.load(pre)
            data = np.asanyarray(image.dataobj)
            bvals, _ = _load_gradients(pre, data.shape[3])
            brain = nib.load(pre_mask).get_fdata() > 0.5
            if brain.shape != data.shape[:3]:
                raise ValueError("mask and preprocessed DWI grids differ")
            return preprocessed_metrics(data, brain, bvals)
        attempt("preprocessed DWI metrics", pre_metrics)

    fa_path, l3_path = find("fa"), find("l3")
    if pre_mask and (fa_path or l3_path):
        def tensor():
            brain = nib.load(pre_mask).get_fdata() > 0.5
            fa = nib.load(fa_path).get_fdata(dtype=np.float32) if fa_path else None
            l3 = nib.load(l3_path).get_fdata(dtype=np.float32) if l3_path else None
            return tensor_metrics(fa, l3, brain)
        attempt("tensor metrics", tensor)

    t1_mask = find("t1_mask")
    if pre_mask and t1_mask:
        attempt("registration Dice", lambda: mask_dice(nib.load(pre_mask), nib.load(t1_mask)))

    before, after = find("input_bvec"), find("rotated_bvec")
    if before and after:
        attempt("b-vector rotation",
                lambda: bvec_rotation(np.loadtxt(before, ndmin=2), np.loadtxt(after, ndmin=2)))

    res["imaging_notes"] = "; ".join(notes)
    return res


def compute_table(cfg: Config) -> pd.DataFrame:
    """One row per roster entry; entries with nothing computable are kept with their reason."""
    img = cfg.imaging
    if not any(img.patterns.values()):
        raise ConfigError("no imaging inputs configured: set imaging.dwi, imaging.mask, ...")
    root = img.root or cfg.inputs.quad_root
    if root is None or not Path(root).is_dir():
        raise ConfigError(f"imaging root is not a directory: {root}")
    records = discovery.discover(cfg).records
    jobs = [(r.participant_id, r.session_id, Path(root), img.patterns) for r in records]
    if img.jobs > 1:
        with ProcessPoolExecutor(img.jobs) as pool:
            rows = list(pool.map(_one, jobs))
    else:
        rows = [_one(job) for job in jobs]
    table = pd.DataFrame(rows)
    first = ["participant_id", "session_id"]
    table = table[first + sorted(c for c in table.columns if c not in first)]
    return table.sort_values(first).reset_index(drop=True)


def write(table: pd.DataFrame, path: Path) -> None:
    write_tsv(table, path)
