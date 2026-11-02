"""Human-readable Markdown summary of a run."""

from __future__ import annotations

import pandas as pd

from . import __version__
from .rules import STATUSES

MEANING = {
    "pass": "No flag on any evaluated rule.",
    "caveat": "Usable; at least one review flag. Consider a sensitivity analysis.",
    "caveat_incomplete_quad": "No eddy_quad metrics; surrogates raised no failure. Inspect "
                              "visually: surrogates are less sensitive than eddy_quad.",
    "incomplete": "A required metric is unavailable, so the assessment is incomplete.",
    "missing": "No usable QC input. Not a statement about data quality.",
    "fail": "Severe threshold or converging review flags. Confirm visually before excluding.",
    "excluded": "Excluded by the operator.",
}


def _md_table(table: pd.DataFrame) -> list[str]:
    cells = table.fillna("").astype(str).replace(r"\|", r"\\|", regex=True)
    lines = ["| " + " | ".join(cells.columns) + " |", "|" + "---|" * len(cells.columns)]
    lines += ["| " + " | ".join(row) + " |" for row in cells.values.tolist()]
    return lines


def render(inventory, decisions, rule_summary, scores, warnings, fallback_enabled) -> str:
    n = len(decisions)
    counts = decisions["status"].value_counts()
    lines = [
        "# quadqc report", "",
        f"quadqc {__version__}. {n} participant-session row(s). Statuses are automated "
        "recommendations from configurable screening rules: review them against the QUAD "
        "reports and your protocol before excluding anyone.", "",
        "## Status summary", "",
    ]
    status_table = pd.DataFrame(
        [{"status": s, "n": int(counts.get(s, 0)), "meaning": MEANING[s]} for s in STATUSES])
    lines += _md_table(status_table)

    lines += ["", "## QUAD input states", ""]
    state_counts = inventory["quad_state"].value_counts().sort_index()
    lines += _md_table(state_counts.rename_axis("quad_state").reset_index(name="n"))

    protocols = inventory.loc[inventory["protocol"] != "", "protocol"].value_counts()
    if len(protocols) > 1:
        lines += ["", "## Mixed protocols", "",
                  "More than one acquisition signature was found. Cohort-relative rules and "
                  "scores assume comparable protocols, so interpret them within protocol.", ""]
        lines += _md_table(protocols.rename_axis("protocol").reset_index(name="n"))

    flagged = decisions[decisions["status"] != "pass"]
    lines += ["", f"## Participants not passing ({len(flagged)})", ""]
    if len(flagged):
        table = flagged[["participant_id", "session_id", "status", "decided_by", "rationale"]]
        lines += _md_table(table.sort_values(["status", "participant_id", "session_id"]))
    else:
        lines.append("None.")

    if len(rule_summary):
        lines += ["", "## Rule calibration", "",
                  "`headroom_sd` is the distance from the observed extreme to the loosest "
                  "threshold in SD of the metric. A large positive value means the rule cannot "
                  "fire on these data; check it is not inert for your protocol.", ""]
        keep = ["pathway", "metric", "n_ok", "n_review", "n_severe", "n_missing",
                "median_review_threshold", "median_severe_threshold", "observed_min",
                "observed_max", "headroom_sd"]
        lines += _md_table(rule_summary[keep].round(4))

    if fallback_enabled:
        used = decisions[decisions["pathway"] == "fallback"]
        lines += ["", "## Fallback pathway", "",
                  f"The optional fallback was enabled and applied to {len(used)} row(s). "
                  "Surrogates are not eddy_quad values and are less sensitive; see "
                  "docs/fallback.md."]

    if scores is not None:
        overall = scores["qc_overall"].dropna()
        lines += ["", "## Scores", "",
                  f"`qc_overall` computed for {len(overall)} of {n} row(s). Higher is worse, "
                  "0 is the cohort median. Scores never set a status."]

    if warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in warnings]
    return "\n".join(lines) + "\n"
