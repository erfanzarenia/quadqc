# Configuration

`quadqc init-config --out quadqc.yaml` writes the full default file with comments. A config
only needs the keys it changes. Unknown keys are errors, with a suggestion for likely typos.
Lists replace the default list entirely, so setting `rules` replaces every default rule.
Relative paths resolve against the config file. `--quad-root` and `--participants` override
the file.

## `inputs`

| Key | Default | Meaning |
|---|---|---|
| `quad_root` | required | Directory searched for `eddy_quad` outputs |
| `participants` | none | Expected roster (TSV, or CSV by extension) |
| `id_column` | `participant_id` | ID column of the participants table |
| `exclusions` | none | Table with `participant_id` and a non-empty `reason` |
| `supplementary` | `[]` | `[{path: x.tsv, id_column: participant_id}]`; columns must not clash |

## `discovery`

| Key | Default | Meaning |
|---|---|---|
| `id_regex` | BIDS `sub-`/`ses-` | Named groups `participant` and optional `session`; first match in the relative path |
| `exclude` | `[]` | fnmatch patterns on relative paths, which also prune traversal: `'*/excluded/*'` |
| `on_duplicate` | `error` | `error`, or `first` for the first sorted path |
| `follow_symlinks` | `false` | Follow directory symlinks |
| `aliases` | `{}` | Map inconsistent labels: `{sub-OLD1: sub-1}` |
| `bvals_glob` | none | Per-volume b-values, relative to `quad_root`, with `{participant}` and `{session}` |

## `decision`

| Key | Default | Meaning |
|---|---|---|
| `convergence_min_domains` | 3 | Review-flagged domains that together cause a failure |
| `min_relative_n` | 20 | Values needed before a relative rule is evaluated; below this it is skipped with a warning |

## `rules`

```yaml
rules:
  - metric: eddy_motion_abs_mm   # a metric column; wildcards are for relative rules only
    domain: motion               # convergence counts distinct domains
    kind: absolute               # absolute (default) or relative
    direction: high              # high: larger is worse; low; both (relative only)
    review: 1.0                  # raise a caveat
    severe: 2.0                  # fail
    unit: voxels                 # absolute (default), or multiply by voxel size
    required: true               # default; if unavailable, the row becomes incomplete
    pathway: quad                # quad (default), or all to also apply on the fallback
    rationale: Why this threshold exists.
  - metric: 'eddy_cnr_b*'
    kind: relative
    domain: signal
    direction: low
    z: 5.0                       # review at this robust z; never fails anyone
```

## `fallback`

Off by default. See [Fallback](fallback.md).

| Key | Meaning |
|---|---|
| `enabled` | Turn on the pathway and score substitutes |
| `required_metrics` | All must be present, or the row stays `missing` |
| `rules` | Replace `pathway: quad` rules for these rows |

## `scoring`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Compute scores |
| `winsor` | 5.0 | Clip each z to this magnitude |
| `min_slot_fraction`, `min_slots` | 0.4, 2 | A family needs both (capped at its slot count) |
| `min_families`, `core_families` | 3, `[motion, signal]` | Conditions for `qc_overall` |
| `min_descriptive_families` | 2 | Condition for `available_family_mean` |
| `families` | see defaults | Replaced as a whole when set |

```yaml
scoring:
  families:
    motion:
      label: Head motion
      slots:
        - name: motion_abs
          metric: eddy_motion_abs_mm
          direction: high
          substitute: {metric: motion_rms_mean_mm, direction: high, kind: alternate-source}
```

## `imaging`

Inputs for `quadqc imaging`. Globs are relative to `root` (default `quad_root`) or absolute, and
must match exactly one file per participant; otherwise the file is not guessed and the reason
goes in `imaging_notes`. See
[Metrics](metrics.md#optional-imaging-metrics) for what each one enables.

Keys: `root`, `jobs`, `dwi`, `mask`, `preprocessed_dwi`, `preprocessed_mask`, `input_bvec`,
`rotated_bvec`, `fa`, `l3`, `t1_mask`.
