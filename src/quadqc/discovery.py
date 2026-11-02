"""Find eddy_quad outputs and build the participant roster.

The roster is the union of the participants table, any participant or session
directory under the root, and every qc.json found. A participant is therefore
reported, never skipped, when its QUAD output is absent or unusable.
"""

from __future__ import annotations

import glob as globlib
import os
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from .config import Config, ConfigError
from .tables import normalise_id, read_table

STUB_SUFFIXES = (".qc", "eddyqc", "eddy_qc", "eddy_quad")


@dataclass
class Record:
    participant_id: str
    session_id: str = ""
    state: str = "absent"  # complete | partial | invalid | failed | absent
    quad_dir: Optional[Path] = None
    candidates: list = field(default_factory=list)
    stubs: list = field(default_factory=list)
    labels: set = field(default_factory=set)
    in_participants_table: Optional[bool] = None
    notes: list = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return self.participant_id, self.session_id


@dataclass
class Discovered:
    records: list
    warnings: list


def match_template(template: str, root: Path, participant: str, session: str) -> list[Path]:
    """Files matching a glob with {participant} and {session}, relative to `root` or absolute."""
    try:
        pattern = template.format(participant=participant, session=session)
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(f"bad placeholder in glob '{template}'; use {{participant}} and "
                          "{session}") from exc
    pattern = pattern.replace("//", "/")
    if not os.path.isabs(pattern):
        pattern = os.path.join(globlib.escape(str(root)), pattern)
    return sorted(Path(p) for p in globlib.glob(pattern, recursive=True))


def _is_excluded(relative: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(relative, p) or fnmatch(f"/{relative}/", p) for p in patterns)


def discover(cfg: Config) -> Discovered:
    root = cfg.inputs.quad_root
    if root is None:
        raise ConfigError("no QUAD root given: set inputs.quad_root or pass --quad-root")
    if not Path(root).is_dir():
        raise ConfigError(f"QUAD root is not a directory: {root}")

    disc = cfg.discovery
    pattern = re.compile(disc.id_regex)
    records: dict[tuple[str, str], Record] = {}
    unmatched: list[str] = []
    warnings: list[str] = []

    def record_for(match: re.Match) -> Record:
        groups = match.groupdict()
        label = normalise_id(groups["participant"])
        participant = normalise_id(disc.aliases.get(label, label))
        session = groups.get("session") or ""
        rec = records.setdefault((participant, session), Record(participant, session))
        rec.labels.add(label)
        return rec

    for current, dirs, files in os.walk(root, followlinks=disc.follow_symlinks):
        relative = os.path.relpath(current, root).replace(os.sep, "/")
        relative = "" if relative == "." else relative
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".")
            and not _is_excluded(f"{relative}/{d}".lstrip("/"), disc.exclude))
        match = pattern.search(relative) if relative else None
        if match and match.end() == len(relative):
            record_for(match)  # a participant or session directory
        if "qc.json" in files:
            if match:
                record_for(match).candidates.append(Path(current))
            else:
                unmatched.append(f"{relative}/qc.json".lstrip("/"))
        elif match and ("qc.pdf" in files or relative.lower().endswith(STUB_SUFFIXES)):
            record_for(match).stubs.append(Path(current))

    if unmatched:
        preview = ", ".join(unmatched[:5])
        raise ConfigError(
            f"{len(unmatched)} qc.json file(s) have no participant ID in their path "
            f"(e.g. {preview}). Set discovery.id_regex, or discovery.exclude to skip them.")

    # A participant with session rows does not also need a session-less row.
    with_sessions = {p for p, s in records if s}
    for key in [k for k in records if not k[1] and k[0] in with_sessions]:
        rec = records[key]
        if not rec.candidates and not rec.stubs:
            del records[key]

    _apply_participants(cfg, records, warnings)

    for rec in records.values():
        rec.candidates.sort()
        if len(rec.candidates) > 1:
            listing = [os.path.relpath(c, root) for c in rec.candidates]
            if disc.on_duplicate == "error":
                raise ConfigError(
                    f"{rec.participant_id} {rec.session_id}".strip()
                    + f" has {len(listing)} QUAD outputs: {listing}. Use discovery.exclude to "
                    "keep one, or discovery.on_duplicate: first.")
            rec.notes.append(f"{len(listing)} QUAD outputs found, used first of {listing}")
        if rec.candidates:
            rec.quad_dir = rec.candidates[0]
        elif rec.stubs:
            rec.state = "failed"
            rec.quad_dir = sorted(rec.stubs)[0]
            rec.notes.append("QUAD directory has no qc.json: eddy_quad probably failed")
    ordered = [records[k] for k in sorted(records)]
    return Discovered(ordered, warnings)


def _apply_participants(cfg: Config, records: dict, warnings: list) -> None:
    path = cfg.inputs.participants
    if path is None:
        warnings.append("no participants table given: participants with no directory under the "
                        "QUAD root cannot be detected as missing")
        return
    table = read_table(path, cfg.inputs.id_column)
    expected = set(table["participant_id"])
    if len(expected) != len(table):
        raise ConfigError(f"{path}: duplicate participant IDs")
    found = {p for p, _ in records}
    for participant in sorted(expected - found):
        records[(participant, "")] = Record(participant)
    for rec in records.values():
        rec.in_participants_table = rec.participant_id in expected
    extra = sorted(found - expected)
    if extra:
        warnings.append(f"{len(extra)} participant(s) under the QUAD root are not in the "
                        f"participants table: {', '.join(extra[:10])}")
