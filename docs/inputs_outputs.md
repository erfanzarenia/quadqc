# Inputs and outputs

## Primary input: eddy_quad output directories

`quadqc` searches `inputs.quad_root` for directories containing `qc.json`. Only `qc.json`
is required. `eddy_msr.txt`, also written by `eddy_quad`, adds residual metrics.

Any depth and naming works, provided the path contains a participant label. These layouts
all work without configuration:

```
derivatives/sub-01/dwi/sub-01_eddy.qc/qc.json                  # BIDS-style derivatives
derivatives/sub-01/ses-1/dwi/sub-01_ses-1_eddy.qc/qc.json      # with sessions
derivatives/batch-3/sub-01/qc/anything/qc.json                 # nested
snakedwi/sub-01/qc/sub-01_eddyqc/qc.json                       # snakedwi
```

For other labels, set `discovery.id_regex`, for example
`'(?P<participant>subj[0-9]+)'`. A `qc.json` with no identifiable participant stops the
run with a clear message, so it can never be skipped by accident.

### The roster

Rows are the union of:

1. the participants table (`inputs.participants`), if given;
2. participant and session directories under the root;
3. every `qc.json` found.

**Give a participants table when you can.** Without one, a participant with no directory
under the root cannot be detected, and `quadqc` warns about this. The table may be TSV or
CSV, and bare labels (`01`) are read as `sub-01`. Only its ID column is read.

### QUAD input states

| `quad_state` | Meaning | Default status |
|---|---|---|
| `complete` | `qc.json` with all core measurements | rules decide |
| `partial` | `qc.json` readable, a core measurement absent or a `-1` sentinel | rules decide; `incomplete` unless a severe rule fires |
| `invalid` | `qc.json` unreadable or not from `eddy_quad` | `missing` |
| `failed` | QUAD directory or `qc.pdf` exists with no `qc.json` | `missing` |
| `absent` | no QUAD output for this participant | `missing` |

Core measurements are absolute motion, relative motion, total outliers and b0 SNR.

### Duplicates

Two `qc.json` files for one participant and session stop the run by default. Remove the
ambiguity with `discovery.exclude`, or set `discovery.on_duplicate: first` to use the first
sorted path and record the choice in `inventory.tsv`. Multiple runs per session are treated
as duplicates.

## Optional inputs

| Key | Format | Purpose |
|---|---|---|
| `inputs.exclusions` | table: `participant_id`, `reason`, optional `session_id` | Manual exclusions. Rows stay in every output. |
| `inputs.supplementary` | table: ID column plus numeric columns | Extra metrics usable in rules and scores, such as MRIQC outputs |
| `discovery.bvals_glob` | glob | Per-volume b-values for within-shell residual scoring |
| `imaging.*` | globs | Inputs for `quadqc imaging` |

Per-volume b-values are looked up from `discovery.bvals_glob`, then from the path recorded
in `qc.json`. If neither resolves, the within-shell residual metrics stay missing.

## Outputs

`quadqc run --out DIR` writes the following. Missing values are `n/a`.

| File | Contents |
|---|---|
| `report.md` | Readable summary: statuses, flagged participants, rule calibration, warnings |
| `decisions.tsv` | One row per participant-session: `status`, `decided_by`, `pathway`, `quad_state`, flags, `missing_metrics`, `exclusion_reason`, `rationale` |
| `rule_evaluations.tsv` | Every rule on every row: value, thresholds as applied, `z`, `level` |
| `rule_summary.tsv` | Per rule: counts by level, observed range, `headroom_sd` |
| `metrics.tsv` | All metrics, including supplementary ones |
| `inventory.tsv` | Roster, `quad_state`, QUAD path, protocol signature, notes |
| `scores.tsv` | Family scores, coverage, `qc_overall`, percentile, substitution summary |
| `score_sources.tsv` | Which metric filled each slot, per participant |
| `score_slots.tsv` | Per slot: counts of primary, substituted and missing values |
| `score_families.tsv` | Per family: required slots, effective dimension, median |
| `provenance.json` | Version, command, resolved config and its hash, package versions, SHA-256 of inputs |

All tables and the report are byte-identical between runs on the same inputs and
configuration. Only `provenance.json` carries a timestamp. Output directories may contain
participant labels and local paths, so review them before sharing.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | `--strict` was given and there were warnings |
| 2 | Configuration or input error; nothing was written |
