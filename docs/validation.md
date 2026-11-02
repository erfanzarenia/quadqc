# Validation

## Automated tests

`pytest` runs on synthetic data only, and no real participant data is in this repository.

| Area | What is checked |
|---|---|
| QUAD parsing | Field meanings, shell-order independence, sentinels with and without flags, MSR within-shell scoring, unusable files |
| Discovery | BIDS and non-BIDS layouts, sessions, roster union, duplicates, exclusions, aliases |
| Rules | Every decision branch, voxel scaling, strict thresholds, convergence, relative rules, fallback constraints |
| Scores | Robust z, direction, winsorising, coverage, no imputation, substitute scaling and logging |
| Pipeline | End-to-end CLI, byte-reproducibility, inputs unmodified, output safety, provenance |
| Imaging | Known translations, rotations, overlaps, invalid voxels, whole-participant reporting |
| Examples | Every example config loads, and the demo dataset runs as documented |
| Regression | Agreement with the original study scripts, below |

## Agreement with the original implementation

`quadqc` was generalised from study-specific scripts. Before refactoring, the original
decision and scoring code was run unmodified on synthetic tables with one engineered case per
decision branch. Its outputs form the fixture in `tests/data/legacy`, which every test run
checks.

## Intentional differences from the original

The regression configuration restores the original rule behaviour where it differs, for
example with `required: false` and study-specific thresholds. New datasets get the right-hand
column by default.

| Original | `quadqc` | Why |
|---|---|---|
| A missing metric passed its rule silently | `required` rules make a row `incomplete` | Absent evidence is not good evidence |
| `-1` sentinels read as measurements | Read as missing | A sentinel is not a measurement |
| `eddy_rot_rms_deg`, `eddy_trans_rms_mm` | `eddy_rot_mean_norm_deg`, `eddy_trans_mean_norm_mm` | Same values; `eddy_quad` averages signed parameters, so they are not RMS |
| `eddy_ec_linear_rms` | `eddy_ec_linear_std_norm` | Same values; it is built from SDs |
| Pooled residual z when b-values were absent | Metric stays missing | A pooled score mostly detects b0 volumes |
| Shells assumed fixed | Read from `qc.json` | Generality |
| Motion thresholds in mm, for one voxel size | Multiples of voxel size | Generality |
| Single phase-encode direction always fails | Review by default | Other corrections exist; configurable |
| CNR thresholds fixed for one protocol | Cohort-relative by default | CNR depends on protocol |
| Fallback used eddy's thresholds on surrogates | Rotation plus cohort-relative rules | Eddy's thresholds do not transfer to surrogates; see [Fallback](fallback.md) |
| Outlier and CNR surrogates in the fallback | Removed | Correspondence too poor to justify them |
| Excluded rows in reference distributions | Left out | A known-bad scan should not shape thresholds |
| Otsu mask if `bet` failed | Mask must be supplied | A silent mask change corrupts metrics |

## Not ported

Dataset-specific or better served by dedicated tools. Any of these can feed rules and scores
as supplementary tables:

- Anatomical IQMs: use [MRIQC](https://mriqc.readthedocs.io), since the originals were
  MRIQC-style approximations with intensity-only segmentation.
- Tractography and parcellation checks tied to one downstream pipeline.
- Raw-data artefact indices (ghosting, ringing, spikes, shading) and cohort-relative flag
  propagation across pipeline stages, which a later decision revision had already superseded.
- Demographic confound screens, per-participant figures and the HTML report.

## Not validated

- Scanners, field strengths, resolutions and shell schemes beyond a single dataset.
- Multi-session and non-BIDS layouts on real data, which were tested only on synthetic trees.
- The thresholds themselves, against visual ratings or outcomes.
