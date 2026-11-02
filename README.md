# quadqc

Cohort-level, traceable quality control for diffusion MRI, built on the per-participant
metrics written by [FSL `eddy_quad`](https://fsl.fmrib.ox.ac.uk/fsl/docs/#/diffusion/eddyqc).

`eddy_quad` reports on one scan at a time, and SQUAD summarises a group. `quadqc` adds
an explicit decision for every participant, with the evidence behind it:

- **One status per participant**, from rules you can read and change in YAML.
- **Nobody is silently dropped.** The roster comes from your participants table and the
  file tree, and absent, crashed, corrupt and partial QUAD outputs are told apart.
- **Missing is never passing.** `-1` sentinels in `qc.json` are read as missing, and a
  rule that cannot be evaluated makes the assessment `incomplete`.
- **Continuous scores** that describe quality without setting any status.
- **Provenance.** Every rule evaluation is recorded, and each run stores its resolved
  configuration, software versions and input checksums. Tables are byte-reproducible.
- **Read-only.** Inputs are never modified and outputs cannot be written inside them.

It works with any pipeline that produces standard `eddy_quad` output, and it does not
need a particular preprocessing pipeline. It is compatible with
[snakedwi](https://github.com/khanlab/snakedwi) from the Khan Lab
([example config](examples/config_snakedwi.yaml)).

## Installation

Requires Python 3.9 or later. FSL is not needed to run `quadqc`, only to produce the
`eddy_quad` outputs it reads.

```bash
git clone <repository-url> quadqc
```

```bash
pip install ./quadqc
```

The optional NIfTI-based metrics need nibabel and SciPy:

```bash
pip install "./quadqc[imaging]"
```

## Quick start

Try it on a synthetic dataset, which also contains one example of each failure mode:

```bash
python quadqc/examples/make_demo_data.py demo_data
```

```bash
quadqc run --quad-root demo_data/derivatives --participants demo_data/participants.tsv --out demo_out
```

Then open `demo_out/report.md`. On your own data, check the inputs first, which writes
nothing:

```bash
quadqc validate --quad-root /path/to/derivatives --participants /path/to/participants.tsv
```

To change thresholds, exclude participants or add metrics, write a configuration:

```bash
quadqc init-config --out quadqc.yaml
```

```bash
quadqc run --config quadqc.yaml --out quadqc_out
```

## What you get

| Status | Meaning |
|---|---|
| `pass` | No flag on any evaluated rule. |
| `caveat` | Usable; at least one review flag. |
| `caveat_incomplete_quad` | Optional fallback only: no QUAD metrics, surrogates raised no failure. Inspect visually. |
| `incomplete` | A required metric is unavailable, so the assessment is incomplete. |
| `missing` | No usable QC input. This says nothing about data quality. |
| `fail` | A severe threshold, or review flags converging across domains. |
| `excluded` | Excluded by you, with a recorded reason. |

Statuses are automated recommendations from screening rules. **The default thresholds are
heuristics, not validated exclusion criteria.** Read [the rationale](docs/metrics.md),
check `rule_summary.tsv` against your protocol, and confirm any `fail` visually against the
QUAD report before excluding anyone.

## Documentation

- [Inputs and outputs](docs/inputs_outputs.md): directory layouts and every output table
- [Metrics and rules](docs/metrics.md): what is measured, justification, interpretation, limits
- [Configuration](docs/configuration.md): every option
- [Fallback for missing QUAD outputs](docs/fallback.md): optional, and less sensitive
- [Validation](docs/validation.md): how correctness was checked
- [Example configurations](examples/)

## Testing

```bash
pip install "./quadqc[dev]"
```

```bash
pytest quadqc
```

## Citing and acknowledgments

If you use `quadqc`, please cite it using [CITATION.cff](CITATION.cff), and cite the tools
it depends on:

- Bastiani M, Cottaar M, Fitzgibbon SP, et al. Automated quality control for within and
  between studies diffusion MRI data using a non-parametric framework for movement and
  distortion correction. *NeuroImage* 2019;184:801-812.
  [doi:10.1016/j.neuroimage.2018.09.073](https://doi.org/10.1016/j.neuroimage.2018.09.073)
- Andersson JLR, Sotiropoulos SN. An integrated approach to correction for off-resonance
  effects and subject movement in diffusion MR imaging. *NeuroImage* 2016;125:1063-1078.
  [doi:10.1016/j.neuroimage.2015.10.019](https://doi.org/10.1016/j.neuroimage.2015.10.019)
- Andersson JLR, Graham MS, Zsoldos E, Sotiropoulos SN. Incorporating outlier detection and
  replacement into a non-parametric framework for movement and distortion correction of
  diffusion MR images. *NeuroImage* 2016;141:556-572.
  [doi:10.1016/j.neuroimage.2016.06.058](https://doi.org/10.1016/j.neuroimage.2016.06.058)

The optional imaging metrics draw on Dietrich et al. (2007, *J Magn Reson Imaging*) for
repeated-acquisition SNR and Oguz et al. (2014, *Front Neuroinform*) for slice-wise signal
loss. `quadqc` was developed using outputs from snakedwi (Khan Lab).

## License

[MIT](LICENSE). `quadqc` does not include or call FSL, which has its own
[licence](https://fsl.fmrib.ox.ac.uk/fsl/docs/#/license), including terms for commercial use.
