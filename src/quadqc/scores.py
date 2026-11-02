"""Continuous family and overall QC scores.

Each metric is signed so higher is worse, expressed as a robust z over its own
cohort distribution, and winsorised. A family is the mean of its available slots and
the overall score is the unweighted mean of families. Scores describe data quality;
they never set a QC status. Missing values are neither imputed nor treated as good.

A substitute is z-scored on its own distribution and only fills a slot whose primary
metric is absent, when the fallback is enabled. Primary and substitute values are
never pooled before scaling, and no primary value is estimated from a substitute.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class Scores:
    scores: pd.DataFrame
    sources: pd.DataFrame
    slots: pd.DataFrame
    families: pd.DataFrame
    warnings: list


def robust_z(values: pd.Series, reference: pd.Series) -> tuple[pd.Series, bool]:
    """(x - median) / (1.4826 * MAD) over the reference rows; SD when MAD is zero."""
    data = pd.to_numeric(values, errors="coerce")
    ref = data[reference]
    if ref.notna().sum() == 0:
        return data * np.nan, False
    median = ref.median()
    mad = (ref - median).abs().median() * 1.4826
    if np.isfinite(mad) and mad != 0:
        return (data - median) / mad, False
    sd = ref.std()
    if np.isfinite(sd) and sd > 0:
        return (data - median) / sd, True
    return data * np.nan, True


def directed_z(values: pd.Series, direction: str, reference: pd.Series,
               winsor: float) -> tuple[pd.Series, bool]:
    z, degenerate = robust_z(values, reference)
    if direction == "low":
        z = -z
    elif direction == "both":
        z = z.abs()
    return z.clip(-winsor, winsor), degenerate


def effective_dimension(z: pd.DataFrame) -> float:
    """Participation ratio of the eigenvalues of the Spearman correlation matrix."""
    present = z.dropna(axis=1, how="all")
    if present.shape[1] < 2:
        return float(present.shape[1])
    corr = present.corr(method="spearman").values
    eig = np.clip(np.linalg.eigvalsh(np.nan_to_num(corr, nan=0.0)), 0, None)
    return float(eig.sum() ** 2 / np.sum(eig ** 2)) if np.sum(eig ** 2) > 0 else np.nan


def compute(metrics: pd.DataFrame, cfg: Config) -> Scores:
    scoring = cfg.scoring
    keys = metrics[["participant_id", "session_id"]].reset_index(drop=True)
    data = metrics.reset_index(drop=True)
    reference = data["exclusion_reason"].fillna("").astype(str) == ""
    warnings: list[str] = []

    def column(name: str) -> pd.Series:
        return data[name] if name in data.columns else pd.Series(np.nan, index=data.index)

    if int(reference.sum()) < cfg.min_relative_n:
        warnings.append(f"only {int(reference.sum())} row(s) to score: cohort-relative scores "
                        f"are unstable below {cfg.min_relative_n}")

    z_slots, names, slot_rows, source_rows = {}, {}, [], []
    for family in scoring.families:
        for slot in family.slots:
            key = f"{family.name}::{slot.name}"
            z, degenerate = directed_z(column(slot.metric), slot.direction, reference,
                                       scoring.winsor)
            source = pd.Series(np.nan, index=data.index, dtype=object)
            source[z.notna()] = slot.metric
            rho, n_both = np.nan, 0
            kind = ""
            sub = slot.substitute if cfg.fallback.enabled else None
            if sub:
                kind = sub.kind
                sub_z, sub_degenerate = directed_z(column(sub.metric), sub.direction, reference,
                                                   scoring.winsor)
                degenerate = degenerate or sub_degenerate
                fill = z.isna() & sub_z.notna()
                z = z.where(~fill, sub_z)
                source[fill] = f"{sub.metric} [{sub.kind}]"
                both = column(slot.metric).notna() & column(sub.metric).notna()
                n_both = int(both.sum())
                if n_both >= 3:
                    rho = round(float(pd.to_numeric(column(slot.metric)[both]).corr(
                        pd.to_numeric(column(sub.metric)[both]), method="spearman")), 3)
            if degenerate:
                warnings.append(f"slot {key}: zero MAD, scaled by SD instead; inspect this "
                                "metric, robust z is unreliable for zero-inflated data")
            z_slots[key], names[key] = z, source
            slot_rows.append({
                "family": family.name, "slot": slot.name, "primary": slot.metric,
                "substitute": sub.metric if sub else "", "kind": kind,
                "n_primary": int((source == slot.metric).sum()),
                "n_substituted": int((source.notna() & (source != slot.metric)).sum()),
                "n_missing": int(source.isna().sum()),
                "rho_primary_substitute": rho, "n_rho": n_both})
            for i in data.index:
                source_rows.append({
                    "participant_id": keys.at[i, "participant_id"],
                    "session_id": keys.at[i, "session_id"], "family": family.name,
                    "slot": slot.name,
                    "metric_source": source[i] if pd.notna(source[i]) else "MISSING"})

    z_table = pd.DataFrame(z_slots, index=data.index)
    source_table = pd.DataFrame(names, index=data.index)

    fam_scores, coverage, family_rows = {}, {}, []
    for family in scoring.families:
        columns = [f"{family.name}::{s.name}" for s in family.slots]
        subset = z_table[columns]
        available = subset.notna().sum(axis=1)
        total = len(columns)
        need = max(min(scoring.min_slots, total), int(np.ceil(scoring.min_slot_fraction * total)))
        score = subset.mean(axis=1, skipna=True).where(available >= need)
        fam_scores[f"qc_{family.name}"] = score.round(3)
        coverage[f"cov_{family.name}"] = available.astype(int).astype(str) + "/" + str(total)
        family_rows.append({
            "family": family.name, "label": family.label, "n_slots": total,
            "min_present_required": need, "effective_dim": round(effective_dimension(subset), 2),
            "n_scored": int(score.notna().sum()),
            "median_score": round(float(score.median()), 3) if score.notna().any() else np.nan})

    fam = pd.DataFrame(fam_scores, index=data.index)
    n_families = fam.notna().sum(axis=1)
    # Family scores are rounded, so exact ties are common; rounding here stops floating-point
    # noise from breaking them differently between numerical library versions.
    mean = fam.mean(axis=1, skipna=True).round(9)
    complete = n_families >= scoring.min_families
    for core in scoring.core_families:
        complete &= fam[f"qc_{core}"].notna()
    overall = mean.where(complete)

    out = pd.concat([keys, fam, pd.DataFrame(coverage, index=data.index)], axis=1)
    out["qc_overall"] = overall.round(3)
    out["qc_overall_pctile"] = (overall.rank(pct=True) * 100).round(1)
    # Same arithmetic over fewer families: descriptive, not comparable to qc_overall.
    out["available_family_mean"] = mean.where(
        n_families >= scoring.min_descriptive_families).round(3)
    out["overall_is_complete"] = complete.astype(int)
    out["n_families_scored"] = n_families.astype(int)
    out["missing_families"] = fam.apply(
        lambda r: ",".join(c[3:] for c in fam.columns if pd.isna(r[c])), axis=1)

    primary = {f"{f.name}::{s.name}": s.metric for f in scoring.families for s in f.slots}
    is_sub = source_table.apply(lambda c: c.notna() & (c != primary[c.name]))
    out["n_substituted"] = is_sub.sum(axis=1).astype(int)
    out["metric_source"] = source_table.apply(
        lambda r: "; ".join(f"{c.split('::')[1]}={r[c]}" for c in source_table.columns
                            if pd.notna(r[c]) and r[c] != primary[c]) or "all primary", axis=1)
    return Scores(out, pd.DataFrame(source_rows), pd.DataFrame(slot_rows),
                  pd.DataFrame(family_rows), warnings)
