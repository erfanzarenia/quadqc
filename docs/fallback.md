# Fallback for participants without QUAD outputs

`eddy_quad` sometimes produces nothing even though `eddy` ran, for example when it crashes
or when its input files were deleted as temporary outputs. By default those participants are
`missing`, the correct conservative answer. This optional pathway assesses them from other
evidence instead, for when losing them matters.

**It is off by default, and its limits should appear in any methods description that uses it.**

## What it does

Set `fallback.enabled: true`, and supply surrogate metrics in a supplementary table (usually
from `quadqc imaging`). Then, for rows whose `qc.json` is `absent`, `failed` or `invalid`:

- Statuses come from `fallback.rules` plus any rule with `pathway: all`.
- If a `fallback.required_metrics` entry is unavailable, the row stays `missing`. The default
  requires a preprocessed-DWI measure, so a scan that was never preprocessed cannot qualify.
- The best possible status is `caveat_incomplete_quad`. **The pathway can never yield `pass`.**
- Score slots may use a declared substitute where the primary metric is absent.

`partial` rows do not use the fallback. Their missing metrics stay missing.

## What it never does

- **No imputation.** No `eddy_*` metric is estimated, and surrogates stay in their own columns.
- **No pooling.** Primary and substitute metrics are each scaled on their own distribution, and
  only the resulting z values are combined.
- **No hidden substitution.** `decisions.tsv` records the `pathway`, and `score_sources.tsv`
  records the metric behind every slot.

## How much it can detect

Sensitivity was checked on one dataset, by asking which motion-flagged participants who **do**
have QUAD metrics the surrogates would have caught. The results may not transfer, and the
figures are not published here.

- **Surrogate scales differ from eddy's, so do not reuse eddy's thresholds on them.** Translation
  estimates ran well below eddy's values, so a rule at eddy's thresholds did not fire. A rule
  that cannot fire makes a participant look clean when they were not really assessed. The
  defaults therefore judge translation against the cohort, which needs surrogates computed for
  **everyone**, not only those lacking QUAD.
- **Rotated b-vectors are the best evidence.** They record what `eddy` actually applied, so keep
  them if you can. Without them, sensitivity drops substantially.
- **Even the best combination missed some flagged participants.** A `caveat_incomplete_quad` row
  was not shown to be clean: treat it as unassessed for motion until it has been inspected, and
  run a sensitivity analysis without these participants.

**Check these on your own data**: `score_slots.tsv` reports `rho_primary_substitute` for each
substitute whenever the fallback is enabled.

Deliberately not offered as substitutes, because correspondence was too poor: slice-wise
outliers for `eddy_outliers_pct_tot`, a raw-data CNR for `eddy_cnr_*`, and any raw measure for
`eddy_ec_linear_std_norm` or `msr_max_z_within_shell`. Those stay missing.

Re-running `eddy` to regenerate QUAD is not a clean alternative either: a re-run gives a
different solution from the original. Such reports suit visual QC, not numeric comparison.

## Setting it up

```bash
pip install "./quadqc[imaging]"
```

```bash
quadqc imaging --config quadqc/examples/config_fallback.yaml --out surrogates.tsv
```

```bash
quadqc run --config quadqc/examples/config_fallback.yaml --out qc_out
```

See [`examples/config_fallback.yaml`](../examples/config_fallback.yaml), with the paths edited
for your data. You may also supply the surrogate table yourself, with the same column names.

## Suggested methods wording

> Participants without eddy_quad metrics (n=X) were assessed with a surrogate pathway: rotation
> from eddy's rotated b-vectors, cohort-relative translation estimated by phase correlation, and
> b0 SNR from repeated acquisitions. No eddy_quad value was estimated. This pathway is less
> sensitive than eddy_quad; these participants were inspected visually, and results were
> repeated without them.
