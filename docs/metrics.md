# Metrics and rules

## How to read this page

There are no consensus cut-offs for excluding diffusion MRI data from these measures, and
appropriate values depend on the protocol, the population and the analysis. **The default
thresholds are screening heuristics.** Each was chosen for the reason stated below, and none
has been validated against an outcome. Motion is a recognised source of spurious group
differences (Yendiki et al. 2014), and automated QC should support inspection, not replace
it (Bastiani et al. 2019).

## QUAD metrics

Field meanings were confirmed against the `eddy_qc` source.

| Metric | Source in `qc.json` | Meaning |
|---|---|---|
| `eddy_motion_abs_mm` | `qc_mot_abs` | Mean displacement from the reference volume, averaged within the mask |
| `eddy_motion_rel_mm` | `qc_mot_rel` | Mean displacement between consecutive volumes |
| `eddy_outliers_pct_tot` | `qc_outliers_tot` | Percentage of slices classified as outliers |
| `eddy_outliers_pct_b<b>` | `qc_outliers_b` | The same, per shell |
| `eddy_outliers_pct_pe<n>` | `qc_outliers_pe` | The same, per phase-encode direction |
| `eddy_snr_b0` | `qc_cnr_avg[0]` | Mean b0 SNR |
| `eddy_cnr_b<b>` | `qc_cnr_avg[1:]` | Mean CNR per shell |
| `eddy_cnr_lowest_shell` | as above | CNR of the lowest non-zero shell, comparable across shell schemes |
| `eddy_trans_mean_norm_mm` | `qc_params_avg[0:3]` | Norm of the **mean signed** translation |
| `eddy_rot_mean_norm_deg` | `qc_params_avg[3:6]` | Norm of the **mean signed** rotation |
| `eddy_ec_linear_std_norm` | `qc_params_avg[6:9]` | Norm of the SDs of the linear eddy-current terms |
| `eddy_s2v_*`, `eddy_field_displ_std_vox` | when `eddy_quad` recorded them | Slice-to-volume motion spread and field displacement spread |
| `eddy_n_pe_dirs`, `eddy_n_b0_vols`, `eddy_n_dw_vols`, `eddy_vox_mm` | `data_*` | Acquisition descriptors |
| `msr_median`, `msr_max` | `eddy_msr.txt` | Per-volume mean squared residual summaries |
| `msr_max_z_within_shell`, `msr_n_vol_z_gt5` | `eddy_msr.txt` plus b-values | Worst volume against its own shell, by robust z |

Two points are easy to misread:

- `eddy_trans_mean_norm_mm` and `eddy_rot_mean_norm_deg` are **not RMS motion**. `eddy_quad`
  averages signed parameters, so opposite movements cancel. They describe a sustained offset
  from the reference. Use `eddy_motion_*` for how much the head moved.
- The within-shell residual needs per-volume b-values, because b0 volumes have much larger
  residuals. Pooled, it would mostly detect b0 volumes, so without b-values it stays missing.

## Default rules

`review` raises a caveat and `severe` fails the participant. Comparisons are strict.

| Metric | Direction | Review | Severe | Why |
|---|---|---|---|---|
| `eddy_motion_abs_mm` | high | 1 voxel | 2 voxels | Expressed in voxels so it transfers across resolutions. |
| `eddy_motion_rel_mm` | high | 0.5 voxel | 1 voxel | A voxel of mean movement between every pair of volumes is sustained, uncontrolled motion. |
| `eddy_outliers_pct_tot` | high | 2% | 5% | Replaced slices are predictions, not measurements, so a high rate reduces independent data. |
| `eddy_snr_b0` | low | 10 | 5 | Near the magnitude noise floor, tensor and CNR estimates become biased. |
| `msr_max_z_within_shell` | high | 50 | none | Set from the development data (range 3 to 81). A prompt to look, so review-only and optional. |
| `eddy_n_pe_dirs` | low | 2 | none | One direction rules out reverse-PE distortion estimation. Review-only, since some protocols use other corrections. |
| `eddy_cnr_b*` | low | robust z ≥ 5 | none | CNR depends strongly on field strength, b-value and resolution, so no absolute default is defensible. |

**Absolute motion can over-flag.** It is measured from the reference volume, so one
repositioning early in a scan gives a high value even if `eddy` corrected it well and relative
motion is low. Check the QUAD motion plot before excluding on this rule alone.

Absolute thresholds are not universal. `rule_summary.tsv` gives `headroom_sd`, the distance
from the observed extreme to the threshold in SD. A rule far from your data may be inert.
Calibrate absolute thresholds on your own protocol rather than reusing values from elsewhere.

## Decision order

The first match wins:

1. Listed in `inputs.exclusions`: `excluded`
2. No usable `qc.json` and fallback off: `missing`
3. No usable `qc.json`, fallback on, required surrogate unavailable: `missing`
4. Any severe threshold breached: `fail`
5. Review flags from absolute rules in at least `convergence_min_domains` domains (default 3): `fail`
6. Fallback pathway: `caveat_incomplete_quad`
7. A required metric unavailable: `incomplete`
8. Any review flag: `caveat`
9. Otherwise: `pass`

Principles behind this:

- **A cohort position cannot fail anyone.** Relative rules only raise caveats and do not count
  toward convergence. In any cohort someone is at the extreme, and that may be real biology.
- **Convergence.** Moderate problems in independent domains can be jointly disqualifying when
  none is alone. Flags in one domain count once, because they share a cause.
- **Do not use outcomes as QC.** Do not add your scientific measures to rules: removing
  participants because a result is unusual biases the result toward the group mean.
- **Excluded rows stay out of reference distributions**, so a known-bad scan cannot shift the
  median or MAD used to judge everyone else.

## Scores

Scores answer a different question from statuses: not "may this be used?" but "how good is
it, and in what way?". **No rule reads a score.** Use them as covariates or for ranking.

1. Each metric is signed so higher is worse (`high`, `low`, or `both` on |z|).
2. Robust z over the cohort, `(x - median) / (1.4826 * MAD)`, winsorised at ±5.
3. A family is the mean of its available slots, so slot count does not change its scale.
4. `qc_overall` is the unweighted mean of families.

Weights are equal because no outcome exists here to estimate them from. Default families:

| Family | Slots |
|---|---|
| `qc_motion` | absolute and relative motion, mean rotation offset, outliers |
| `qc_signal` | b0 SNR, lowest-shell CNR |
| `qc_correction` | eddy-current spread, within-shell residual |

Nothing is imputed. A family needs at least `min_slots` slots and `min_slot_fraction` of them.
`qc_overall` needs `min_families` and every `core_families` entry; otherwise only the descriptive
`available_family_mean` is given, which **must not be ranked with `qc_overall`**.

### Limitations of scores

- A score of 0 means typical for this cohort, not good. Scores from different cohorts or
  protocols are not comparable.
- Scores are pooled across the cohort, so they carry real group differences such as age and
  motion. Test for this before using them as covariates.
- Robust z fails on zero-inflated metrics. When MAD is zero the SD is used instead and a
  warning is issued; inspect that metric.
- `both`-direction metrics have no negative range, which raises their family's floor.
- Equal weights are not equal influence: correlated slots pull harder. `effective_dim` in
  `score_families.tsv` shows how much independent information each family carries.

## General limitations

- Sites or protocols are not modelled. Mixed protocols trigger a warning. For multi-site data,
  run each site separately, because relative rules and scores assume comparable acquisitions.
- `quadqc` summarises what `eddy_quad` measured. It does not view images, and it cannot detect
  artefacts `eddy` does not model. Visual inspection is still needed.
- `qc_mot_abs`, `qc_mot_rel` and the CNR values are rounded by `eddy_quad` to two decimals.
- Default thresholds were chosen on a single dataset and have not been tested
  on other scanners. See [Validation](validation.md).

## Optional imaging metrics

`quadqc imaging` computes these from NIfTI files into a supplementary table. Add them to rules
if you want them enforced; see
[`examples/config_with_imaging.yaml`](../examples/config_with_imaging.yaml).

| Metric | Input | Meaning |
|---|---|---|
| `preproc_n_nonfinite` | preprocessed DWI | Non-finite voxels |
| `preproc_pct_negative_in_brain` | preprocessed DWI, mask | Negative samples inside the brain |
| `preproc_b0_snr_repeat` | preprocessed DWI, mask | Median voxelwise mean/SD over repeated b0s, in a twice-eroded mask |
| `fa_pct_gt1` | dtifit FA, mask | Brain voxels with FA above 1 |
| `pct_negative_eigenvalue` | dtifit L3, mask | Brain voxels with a negative third eigenvalue |
| `reg_dice_dwimask_t1mask` | DWI mask, T1w mask | Mask overlap, using image affines |
| `motion_rms_mean_mm`, `motion_rel_mean_mm` | pre-eddy DWI | Translation-only phase-correlation motion, per shell |
| `pct_slice_outliers`, `n_vol_with_ge3_outlier_slices` | pre-eddy DWI, mask | Within-shell slice signal loss (z < -3) |
| `n_vol_dropout`, `pct_vol_dropout` | pre-eddy DWI, mask | Whole-volume signal loss (z < -3) |
| `bvecrot_rms_deg`, `bvecrot_max_deg` | input and rotated b-vectors | Rotation `eddy` applied |

These assume axial slices along the third image axis and a mask you supply. No mask is
derived automatically, because a poor one silently corrupts every metric.

## References

- Bastiani M, et al. *NeuroImage* 2019;184:801-812.
- Dietrich O, et al. Measurement of signal-to-noise ratios in MR images. *J Magn Reson Imaging*
  2007;26:375-385.
- Oguz I, et al. DTIPrep: quality control of diffusion-weighted images. *Front Neuroinform*
  2014;8:4.
- Yendiki A, et al. Spurious group differences due to head motion in a diffusion MRI study.
  *NeuroImage* 2014;88:79-90.
