"""End-to-end run: discover, extract, merge, decide, score, report, record provenance."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from importlib import metadata
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import __version__, discovery, quad, report, rules, scores
from .config import Config, ConfigError
from .tables import first_duplicate, read_table, resolve_id_keys, to_numeric, write_tsv

log = logging.getLogger("quadqc")


@dataclass
class Cohort:
    inventory: pd.DataFrame
    metrics: pd.DataFrame
    warnings: list
    input_files: list


def _find_bvals(rec, qc: dict, cfg: Config, notes: list) -> Optional[np.ndarray]:
    root = cfg.inputs.quad_root
    template = cfg.discovery.bvals_glob
    if template:
        hits = discovery.match_template(template, root, rec.participant_id, rec.session_id)
        if len(hits) != 1:
            notes.append(f"bvals_glob matched {len(hits)} files")
            return None
        return quad.read_bvals(hits[0])
    recorded = qc.get("data_file_bvals")
    if not recorded:
        return None
    candidate = Path(recorded)
    if candidate.is_absolute():
        return quad.read_bvals(candidate) if candidate.is_file() else None
    for parent in [rec.quad_dir, *rec.quad_dir.parents]:
        if (parent / candidate).is_file():
            return quad.read_bvals(parent / candidate)
        if parent == root:
            break
    return None


def _identity_note(rec, qc: dict, cfg: Config) -> Optional[str]:
    """Warn when qc.json names a different participant from the path it was found at."""
    pattern = re.compile(cfg.discovery.id_regex)
    for key in ("qc_path", "data_file_eddy"):
        match = pattern.search(str(qc.get(key) or ""))
        if match:
            label = match.group("participant")
            if label not in rec.labels and cfg.discovery.aliases.get(
                    label, label) != rec.participant_id:
                return f"qc.json field {key} names {label}, not {rec.participant_id}"
    return None


def _protocol(qc: Optional[dict]) -> str:
    if not qc:
        return ""
    shells = ",".join(str(int(b)) for b in qc.get("data_unique_bvals") or [])
    vols = (qc.get("data_no_b0_vols") or 0) + (qc.get("data_no_dw_vols") or 0)
    vox = "x".join(f"{v:g}" for v in (qc.get("data_vox_size") or []))
    return f"b={shells} n={vols} vox={vox}"


def _merge_supplementary(metrics: pd.DataFrame, cfg: Config, warnings: list,
                         input_files: list) -> pd.DataFrame:
    for item in cfg.inputs.supplementary:
        path = item["path"]
        table = read_table(path, item["id_column"])
        keys = resolve_id_keys(table)
        dup = first_duplicate(table, keys)
        if dup:
            raise ConfigError(f"{path}: duplicate row for {dup}")
        numeric, dropped = to_numeric(table, keys)
        dropped = [c for c in dropped if not c.endswith("_notes")]
        if dropped:
            warnings.append(f"{path.name}: non-numeric column(s) ignored: {', '.join(dropped)}")
        clash = (set(numeric.columns) - set(keys)) & set(metrics.columns)
        if clash:
            raise ConfigError(f"{path}: column(s) already defined: {sorted(clash)}")
        unknown = set(table["participant_id"]) - set(metrics["participant_id"])
        if unknown:
            warnings.append(f"{path.name}: {len(unknown)} ID(s) not in the roster were ignored: "
                            + ", ".join(sorted(unknown)[:10]))
        metrics = metrics.merge(numeric, how="left", on=list(keys))
        input_files.append(path)
    return metrics


def _read_exclusions(cfg: Config, metrics: pd.DataFrame, warnings: list, input_files: list):
    reasons = pd.Series("", index=metrics.index, dtype=object)
    path = cfg.inputs.exclusions
    if path is None:
        return reasons
    table = read_table(path, "participant_id", required=("reason",))
    empty = table[table["reason"].str.strip() == ""]
    if len(empty):
        raise ConfigError(f"{path}: every exclusion needs a reason; none given for "
                          f"{empty['participant_id'].iloc[0]}")
    input_files.append(path)
    has_session = "session_id" in table.columns
    for _, row in table.iterrows():
        mask = metrics["participant_id"] == row["participant_id"]
        if has_session and row["session_id"]:
            mask &= metrics["session_id"] == row["session_id"]
        if not mask.any():
            warnings.append(f"exclusion for {row['participant_id']} matches no roster row")
        reasons[mask] = row["reason"]
    return reasons


def build_cohort(cfg: Config) -> Cohort:
    """Read every input into the inventory and metric tables. Writes nothing."""
    found = discovery.discover(cfg)
    warnings = list(found.warnings)
    root = cfg.inputs.quad_root
    inv_rows, metric_rows, input_files = [], [], []

    for rec in found.records:
        metrics: dict = {}
        qc = None
        if rec.state != "failed" and rec.quad_dir is not None:
            path = rec.quad_dir / "qc.json"
            try:
                qc = quad.load_qc_json(path)
            except quad.QuadError as exc:
                rec.state = "invalid"
                rec.notes.append(str(exc).replace(str(root), "<quad_root>"))
            else:
                input_files.append(path)
                msr = rec.quad_dir / "eddy_msr.txt"
                if msr.is_file():
                    input_files.append(msr)
                try:
                    bvals = _find_bvals(rec, qc, cfg, rec.notes)
                except ConfigError:
                    raise
                except (ValueError, OSError) as exc:
                    bvals = None
                    rec.notes.append(f"could not read b-values: {exc}")
                metrics, notes = quad.extract(qc, msr, bvals)
                rec.notes.extend(notes)
                rec.state = quad.state_of(metrics)
                identity = _identity_note(rec, qc, cfg)
                if identity:
                    rec.notes.append(identity)
                    warnings.append(identity)
        inv_rows.append({
            "participant_id": rec.participant_id, "session_id": rec.session_id,
            "in_participants_table": {None: "", True: "1", False: "0"}[
                rec.in_participants_table],
            "quad_state": rec.state,
            "quad_dir": os.path.relpath(rec.quad_dir, root) if rec.quad_dir else "",
            "n_quad_outputs": len(rec.candidates), "protocol": _protocol(qc),
            "notes": "; ".join(rec.notes)})
        metric_rows.append({"participant_id": rec.participant_id, "session_id": rec.session_id,
                            "quad_state": rec.state, **metrics})

    inventory = pd.DataFrame(inv_rows, columns=[
        "participant_id", "session_id", "in_participants_table", "quad_state", "quad_dir",
        "n_quad_outputs", "protocol", "notes"])
    table = pd.DataFrame(metric_rows)
    if table.empty:
        raise ConfigError(f"no participants or eddy_quad outputs found under {root}")
    fixed = ["participant_id", "session_id", "quad_state"]
    table = table[fixed + sorted(c for c in table.columns if c not in fixed)]
    table = _merge_supplementary(table, cfg, warnings, input_files)
    table.insert(3, "exclusion_reason", _read_exclusions(cfg, table, warnings, input_files))

    protocols = inventory.loc[inventory["protocol"] != "", "protocol"].nunique()
    if protocols > 1:
        warnings.append(f"{protocols} acquisition protocols found: cohort-relative rules and "
                        "scores assume comparable protocols")
    n_unusable = int(inventory["quad_state"].isin(["absent", "failed", "invalid"]).sum())
    if n_unusable:
        warnings.append(f"{n_unusable} row(s) have no usable qc.json")
    warnings.extend(_unknown_metric_warnings(cfg, table))
    return Cohort(inventory, table, warnings, input_files)


def _unknown_metric_warnings(cfg: Config, table: pd.DataFrame) -> list[str]:
    active = list(cfg.rules) + (list(cfg.fallback.rules) if cfg.fallback.enabled else [])
    out = []
    for rule in active:
        if "*" in rule.metric:
            if not any(fnmatchcase(c, rule.metric) for c in table.columns):
                out.append(f"rule pattern {rule.metric} matches no metric: rule has no effect")
        elif rule.metric not in table.columns:
            effect = "participants become incomplete" if rule.required else "rule has no effect"
            out.append(f"rule metric {rule.metric} is not available for anyone: {effect}")
    if cfg.fallback.enabled:
        absent = [m for m in cfg.fallback.required_metrics if m not in table.columns]
        if absent:
            out.append("fallback is enabled but required surrogate(s) are absent from every "
                       f"table: {', '.join(absent)}. Run `quadqc imaging` or supply them.")
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _provenance(cfg: Config, cohort: Cohort) -> dict:
    root = cfg.inputs.quad_root
    versions = {}
    for package in ("numpy", "pandas", "pyyaml"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "unknown"
    files = []
    for path in cohort.input_files:
        try:
            shown = str(Path(path).relative_to(root))
        except ValueError:
            shown = str(path)
        files.append({"path": shown, "sha256": _sha256(Path(path))})
    return {
        "tool": "quadqc", "version": __version__,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": " ".join(shlex.quote(a) for a in sys.argv),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": versions, "quad_root": str(root), "config": cfg.resolved,
        "config_sha256": hashlib.sha256(
            json.dumps(cfg.resolved, sort_keys=True, default=str).encode()).hexdigest(),
        "input_files": sorted(files, key=lambda f: f["path"]),
    }


def refuse_input_overlap(out: Path, cfg: Config) -> None:
    """Inputs are read-only: outputs may not be written inside any input tree."""
    out = out.resolve()
    for root in (cfg.inputs.quad_root, cfg.imaging.root):
        if root is not None and (out == root.resolve() or root.resolve() in out.parents):
            raise ConfigError(f"output {out} is inside the input tree {root}: inputs are read-only")


def _prepare_output(out: Path, cfg: Config, overwrite: bool) -> None:
    refuse_input_overlap(out, cfg)
    out = out.resolve()
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise ConfigError(f"output directory {out} is not empty; use --overwrite to replace it")
    out.mkdir(parents=True, exist_ok=True)


def run(cfg: Config, out: Path, overwrite: bool = False) -> dict:
    """Run the pipeline and write all outputs. Returns the result tables."""
    cohort = build_cohort(cfg)  # fail on bad inputs before touching the output directory
    _prepare_output(out, cfg, overwrite)

    decided = rules.evaluate(cohort.metrics, cfg)
    warnings = cohort.warnings + decided.warnings
    scored = scores.compute(cohort.metrics, cfg) if cfg.scoring.enabled else None
    if scored:
        warnings = warnings + scored.warnings
    warnings = list(dict.fromkeys(warnings))

    write_tsv(cohort.inventory, out / "inventory.tsv")
    write_tsv(cohort.metrics, out / "metrics.tsv")
    write_tsv(decided.decisions, out / "decisions.tsv")
    write_tsv(decided.evaluations, out / "rule_evaluations.tsv")
    write_tsv(decided.rule_summary, out / "rule_summary.tsv")
    if scored:
        write_tsv(scored.scores, out / "scores.tsv")
        write_tsv(scored.sources, out / "score_sources.tsv")
        write_tsv(scored.slots, out / "score_slots.tsv")
        write_tsv(scored.families, out / "score_families.tsv")
    (out / "report.md").write_text(report.render(
        cohort.inventory, decided.decisions, decided.rule_summary,
        scored.scores if scored else None, warnings, cfg.fallback.enabled))
    (out / "provenance.json").write_text(
        json.dumps(_provenance(cfg, cohort), indent=2, sort_keys=True, default=str) + "\n")
    for warning in warnings:
        log.warning(warning)
    return {"cohort": cohort, "decisions": decided, "scores": scored, "warnings": warnings}
