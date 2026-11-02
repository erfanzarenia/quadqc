"""Turn metrics into one traceable QC status per participant.

Order of evaluation:

1. manual exclusion                          -> excluded
2. no usable qc.json, fallback off           -> missing
3. no usable qc.json, surrogates insufficient -> missing
4. any severe threshold breached             -> fail
5. review flags in enough absolute domains   -> fail
6. fallback pathway                          -> caveat_incomplete_quad
7. a required metric unavailable             -> incomplete
8. any review flag                           -> caveat
9. otherwise                                 -> pass

Relative rules are review-only: a cohort position can raise a caveat but cannot,
alone or by convergence, fail a participant. Missing values never count as passing.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

import numpy as np
import pandas as pd

from .config import Config, Rule

PASS, CAVEAT, CAVEAT_INCOMPLETE = "pass", "caveat", "caveat_incomplete_quad"
INCOMPLETE, MISSING, FAIL, EXCLUDED = "incomplete", "missing", "fail", "excluded"
STATUSES = (PASS, CAVEAT, CAVEAT_INCOMPLETE, INCOMPLETE, MISSING, FAIL, EXCLUDED)
NO_QUAD_STATES = ("absent", "failed", "invalid")
VOXEL_COLUMNS = ("eddy_vox_mm", "vox_mm")


@dataclass
class Decisions:
    decisions: pd.DataFrame
    evaluations: pd.DataFrame
    rule_summary: pd.DataFrame
    warnings: list


def _expand(rules: tuple, columns) -> list[Rule]:
    """Expand wildcard metrics against the available metric columns."""
    out = []
    for rule in rules:
        if "*" in rule.metric:
            names = sorted(c for c in columns if fnmatchcase(c, rule.metric))
            out.extend(_with_metric(rule, n) for n in names)
        else:
            out.append(rule)
    return out


def _with_metric(rule: Rule, metric: str) -> Rule:
    fields = {k: getattr(rule, k) for k in rule.__dataclass_fields__}
    fields["metric"] = metric
    return Rule(**fields)


def _breach(value: float, direction: str, threshold) -> bool:
    if threshold is None:
        return False
    return value > threshold if direction == "high" else value < threshold


def _voxel_size(row: pd.Series) -> float:
    for column in VOXEL_COLUMNS:
        value = row.get(column, np.nan)
        if pd.notna(value):
            return float(value)
    return np.nan


def _absolute(rule: Rule, row: pd.Series) -> dict:
    value = row.get(rule.metric, np.nan)
    value = np.nan if pd.isna(value) else float(value)
    scale = 1.0
    if rule.unit == "voxels":
        scale = _voxel_size(row)
    review = None if rule.review is None else rule.review * scale
    severe = None if rule.severe is None else rule.severe * scale
    if not np.isfinite(value) or not np.isfinite(scale):
        level = "missing"
    elif _breach(value, rule.direction, severe):
        level = "severe"
    elif _breach(value, rule.direction, review):
        level = "review"
    else:
        level = "ok"
    return {"value": value, "review_threshold": review, "severe_threshold": severe,
            "z": np.nan, "z_threshold": np.nan, "level": level}


def _reference(rule: Rule, metrics: pd.DataFrame, ref: pd.Series, min_n: int):
    """Median and robust scale of a metric over the reference rows, or None."""
    if rule.metric not in metrics.columns:
        return None
    values = pd.to_numeric(metrics.loc[ref, rule.metric], errors="coerce").dropna()
    if len(values) < min_n:
        return None
    median = values.median()
    mad = (values - median).abs().median() * 1.4826
    return None if mad <= 0 else (float(median), float(mad))


def _relative(rule: Rule, row: pd.Series, reference) -> dict:
    value = row.get(rule.metric, np.nan)
    value = np.nan if pd.isna(value) else float(value)
    out = {"value": value, "review_threshold": None, "severe_threshold": None,
           "z": np.nan, "z_threshold": rule.z, "level": "missing"}
    if not np.isfinite(value):
        return out
    if reference is None:
        out["level"] = "not_evaluated"
        return out
    z = (value - reference[0]) / reference[1]
    signed = {"high": z, "low": -z, "both": abs(z)}[rule.direction]
    out["z"] = round(float(signed), 3)
    out["level"] = "review" if signed >= rule.z else "ok"
    return out


def _flag(rule: Rule, result: dict) -> str:
    value = f"{result['value']:.4g}"
    if rule.kind == "relative":
        return f"[{rule.domain}] {rule.metric}={value} (z={result['z']:.1f} >= {rule.z:g})"
    sign = ">" if rule.direction == "high" else "<"
    bound = result["severe_threshold"] if result["level"] == "severe" else (
        result["review_threshold"])
    return f"[{rule.domain}] {rule.metric}={value} ({sign}{bound:.4g} {result['level']})"


def evaluate(metrics: pd.DataFrame, cfg: Config) -> Decisions:
    """Evaluate all rules. `metrics` holds id columns, quad_state, exclusion_reason, metrics."""
    warnings: list[str] = []
    excluded = metrics["exclusion_reason"].fillna("").astype(str) != ""
    ref = ~excluded
    primary = _expand(cfg.rules, metrics.columns)
    shared = [r for r in primary if r.pathway == "all"]
    secondary = shared + _expand(cfg.fallback.rules, metrics.columns)

    references = {}
    for rule in primary + secondary:
        if rule.kind == "relative" and rule.metric not in references:
            references[rule.metric] = _reference(rule, metrics, ref, cfg.min_relative_n)
            if references[rule.metric] is None and rule.metric in metrics.columns:
                warnings.append(
                    f"relative rule on {rule.metric} not evaluated: fewer than "
                    f"{cfg.min_relative_n} values or zero spread")

    rows, evaluations = [], []
    for _, row in metrics.iterrows():
        no_quad = row["quad_state"] in NO_QUAD_STATES
        use_fallback = no_quad and cfg.fallback.enabled
        pathway = "fallback" if use_fallback else ("quad" if not no_quad else "none")
        active = secondary if use_fallback else (primary if not no_quad else [])

        severe, review, missing, domains = [], [], [], set()
        for rule in active:
            if rule.kind == "relative":
                result = _relative(rule, row, references.get(rule.metric))
            else:
                result = _absolute(rule, row)
            evaluations.append({
                "participant_id": row["participant_id"], "session_id": row["session_id"],
                "pathway": pathway, "metric": rule.metric, "domain": rule.domain,
                "kind": rule.kind, "direction": rule.direction, "unit": rule.unit, **result})
            level = result["level"]
            if level == "severe":
                severe.append(_flag(rule, result))
            elif level == "review":
                review.append(_flag(rule, result))
                if rule.kind == "absolute":
                    domains.add(rule.domain)
            elif level == "missing" and rule.required:
                missing.append(rule.metric)

        status, decided_by, why = _decide(row, cfg, no_quad, use_fallback, severe, review,
                                          missing, domains)
        rows.append({
            "participant_id": row["participant_id"], "session_id": row["session_id"],
            "status": status, "decided_by": decided_by, "pathway": pathway,
            "quad_state": row["quad_state"], "severe_flags": "; ".join(severe),
            "review_flags": "; ".join(review), "missing_metrics": ", ".join(missing),
            "exclusion_reason": row["exclusion_reason"] or "", "rationale": why})

    decisions = pd.DataFrame(rows, columns=[
        "participant_id", "session_id", "status", "decided_by", "pathway", "quad_state",
        "severe_flags", "review_flags", "missing_metrics", "exclusion_reason", "rationale"])
    eval_table = pd.DataFrame(evaluations, columns=[
        "participant_id", "session_id", "pathway", "metric", "domain", "kind", "direction", "unit",
        "value", "review_threshold", "severe_threshold", "z", "z_threshold", "level"])
    return Decisions(decisions, eval_table, _summarise(eval_table), warnings)


def _decide(row, cfg, no_quad, use_fallback, severe, review, missing, domains):
    if row["exclusion_reason"]:
        return EXCLUDED, "exclusion", f"excluded by the operator: {row['exclusion_reason']}"
    if no_quad and not use_fallback:
        return MISSING, "no_quad_output", (
            f"QUAD output {row['quad_state']}; no quality judgement is possible")
    if use_fallback:
        absent = [m for m in cfg.fallback.required_metrics
                  if pd.isna(row.get(m, np.nan))]
        if absent:
            return MISSING, "insufficient_fallback_evidence", (
                f"QUAD output {row['quad_state']} and required surrogate(s) unavailable: "
                + ", ".join(absent))
    if severe:
        return FAIL, "severe_threshold", "severe threshold breached: " + "; ".join(severe)
    if len(domains) >= cfg.convergence_min_domains:
        return FAIL, "convergence", (
            f"review flags converge across {len(domains)} domains "
            f"({', '.join(sorted(domains))}): " + "; ".join(review))
    if use_fallback:
        extra = f"; elevated: {'; '.join(review)}" if review else ""
        return CAVEAT_INCOMPLETE, "fallback_pathway", (
            f"no eddy_quad metrics (QUAD output {row['quad_state']}); no failure detected by "
            f"lower-sensitivity surrogates{extra}. Inspect this participant visually")
    if missing:
        return INCOMPLETE, "missing_required_metric", (
            "required metric(s) unavailable, so the assessment is incomplete: "
            + ", ".join(missing))
    if review:
        return CAVEAT, "review_threshold", "review flag(s): " + "; ".join(review)
    return PASS, "no_flags", "all evaluated rules within thresholds"


def _median(values: pd.Series) -> float:
    numbers = pd.to_numeric(values, errors="coerce").dropna()
    return float(numbers.median()) if len(numbers) else np.nan


def _summarise(evaluations: pd.DataFrame) -> pd.DataFrame:
    """Per-rule counts and how far each threshold sits from the observed data.

    headroom_sd is the distance from the observed extreme to the loosest threshold,
    in SD of the metric. A rule that fires on nobody and sits far beyond the data
    may be inert for this protocol, and that should be visible rather than reassuring.
    """
    rows = []
    for (pathway, metric, kind), group in evaluations.groupby(
            ["pathway", "metric", "kind"], sort=False):
        values = pd.to_numeric(group["value"], errors="coerce").dropna()
        review = _median(group["review_threshold"])
        severe = _median(group["severe_threshold"])
        loosest = review if pd.notna(review) else severe
        headroom = np.nan
        if kind == "absolute" and len(values) > 1 and values.std() > 0 and pd.notna(loosest):
            if group["direction"].iloc[0] == "high":
                headroom = round(float((loosest - values.max()) / values.std()), 2)
            else:
                headroom = round(float((values.min() - loosest) / values.std()), 2)
        rows.append({
            "pathway": pathway, "metric": metric, "kind": kind, "domain": group["domain"].iloc[0],
            "median_review_threshold": review, "median_severe_threshold": severe,
            "z_threshold": _median(group["z_threshold"]),
            "n_ok": int((group["level"] == "ok").sum()),
            "n_review": int((group["level"] == "review").sum()),
            "n_severe": int((group["level"] == "severe").sum()),
            "n_missing": int(group["level"].isin(["missing", "not_evaluated"]).sum()),
            "observed_min": values.min() if len(values) else np.nan,
            "observed_median": values.median() if len(values) else np.nan,
            "observed_max": values.max() if len(values) else np.nan,
            "headroom_sd": headroom})
    return pd.DataFrame(rows)
