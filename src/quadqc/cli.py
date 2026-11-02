"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, config, pipeline
from .config import ConfigError

log = logging.getLogger("quadqc")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="YAML configuration file")
    parser.add_argument("--quad-root", help="directory searched for eddy_quad outputs")
    parser.add_argument("--participants", help="participants table: the expected roster")


def _load(args) -> config.Config:
    return config.load(args.config, {"quad_root": args.quad_root,
                                     "participants": args.participants})


def _cmd_init(args) -> int:
    text = config.default_config_text()
    if args.out is None:
        sys.stdout.write(text)
        return 0
    if args.out.exists() and not args.overwrite:
        raise ConfigError(f"{args.out} exists; use --overwrite to replace it")
    args.out.write_text(text)
    log.info("wrote %s", args.out)
    return 0


def _cmd_validate(args) -> int:
    cfg = _load(args)
    cohort = pipeline.build_cohort(cfg)
    states = cohort.inventory["quad_state"].value_counts().sort_index()
    print(f"{len(cohort.inventory)} participant-session row(s) under {cfg.inputs.quad_root}")
    for state, count in states.items():
        print(f"  {state}: {count}")
    for warning in cohort.warnings:
        print(f"WARNING: {warning}")
    if args.strict and cohort.warnings:
        return 1
    return 0


def _cmd_run(args) -> int:
    cfg = _load(args)
    result = pipeline.run(cfg, args.out, overwrite=args.overwrite)
    counts = result["decisions"].decisions["status"].value_counts().sort_index()
    print(f"Wrote results to {args.out}")
    for status, count in counts.items():
        print(f"  {status}: {count}")
    return 1 if args.strict and result["warnings"] else 0


def _cmd_imaging(args) -> int:
    try:
        from . import imaging
    except ImportError as exc:
        raise ConfigError("`quadqc imaging` needs optional dependencies: "
                          "pip install 'quadqc[imaging]'") from exc
    cfg = _load(args)
    pipeline.refuse_input_overlap(args.out, cfg)
    if args.out.exists() and not args.overwrite:
        raise ConfigError(f"{args.out} exists; use --overwrite to replace it")
    table = imaging.compute_table(cfg)
    imaging.write(table, args.out)
    print(f"Wrote {args.out} ({len(table)} row(s))")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quadqc", description=__doc__)
    parser.add_argument("--version", action="version", version=f"quadqc {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show progress messages")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-config", help="write the default configuration")
    init.add_argument("--out", type=Path, help="destination (default: stdout)")
    init.add_argument("--overwrite", action="store_true")
    init.set_defaults(func=_cmd_init)

    check = sub.add_parser("validate", help="check configuration and inputs; writes nothing")
    _common(check)
    check.add_argument("--strict", action="store_true", help="exit 1 if there are warnings")
    check.set_defaults(func=_cmd_validate)

    run = sub.add_parser("run", help="run QC and write all outputs")
    _common(run)
    run.add_argument("--out", type=Path, required=True, help="output directory")
    run.add_argument("--overwrite", action="store_true", help="allow a non-empty --out")
    run.add_argument("--strict", action="store_true", help="exit 1 if there are warnings")
    run.set_defaults(func=_cmd_run)

    img = sub.add_parser("imaging", help="compute optional NIfTI-based metrics into a table")
    _common(img)
    img.add_argument("--out", type=Path, required=True, help="output TSV")
    img.add_argument("--overwrite", action="store_true")
    img.set_defaults(func=_cmd_imaging)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s: %(message)s")
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
