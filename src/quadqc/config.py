"""Configuration loading and validation."""

from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass, field, fields
from importlib import resources
from pathlib import Path
from typing import Any, Optional

import yaml


class ConfigError(ValueError):
    """The configuration or the inputs it names cannot be used."""


@dataclass(frozen=True)
class Rule:
    metric: str
    domain: str
    kind: str = "absolute"  # absolute | relative
    direction: str = "high"  # high = larger is worse; both is relative-only
    review: Optional[float] = None
    severe: Optional[float] = None
    z: Optional[float] = None
    unit: str = "absolute"  # absolute | voxels (thresholds multiply voxel size)
    required: bool = True  # absence makes the participant incomplete
    pathway: str = "quad"  # quad | all (also applied on the fallback pathway)
    rationale: str = ""

    def __post_init__(self) -> None:
        name = f"rule '{self.metric}'"
        _choice(self.kind, ("absolute", "relative"), f"{name}: kind")
        _choice(self.direction, ("high", "low", "both"), f"{name}: direction")
        _choice(self.unit, ("absolute", "voxels"), f"{name}: unit")
        _choice(self.pathway, ("quad", "all"), f"{name}: pathway")
        if self.kind == "relative":
            if self.z is None or self.z <= 0:
                raise ConfigError(f"{name}: a relative rule needs a positive z")
            if self.review is not None or self.severe is not None:
                raise ConfigError(f"{name}: a relative rule cannot set review or severe")
            return
        if self.direction == "both":
            raise ConfigError(f"{name}: direction 'both' is only valid for relative rules")
        if "*" in self.metric:
            raise ConfigError(f"{name}: wildcards are only valid for relative rules")
        if self.review is None and self.severe is None:
            raise ConfigError(f"{name}: an absolute rule needs review and/or severe")
        if self.review is not None and self.severe is not None:
            ordered = self.review < self.severe if self.direction == "high" else (
                self.review > self.severe)
            if not ordered:
                raise ConfigError(
                    f"{name}: review={self.review} must be less extreme than "
                    f"severe={self.severe} for direction '{self.direction}'")


@dataclass(frozen=True)
class Substitute:
    metric: str
    direction: str = "high"
    kind: str = "alternate-source"


@dataclass(frozen=True)
class Slot:
    name: str
    metric: str
    direction: str = "high"
    substitute: Optional[Substitute] = None


@dataclass(frozen=True)
class Family:
    name: str
    label: str
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class Scoring:
    enabled: bool
    winsor: float
    min_slot_fraction: float
    min_slots: int
    min_families: int
    core_families: tuple[str, ...]
    min_descriptive_families: int
    families: tuple[Family, ...]


@dataclass(frozen=True)
class Fallback:
    enabled: bool
    required_metrics: tuple[str, ...]
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Discovery:
    id_regex: str
    exclude: tuple[str, ...]
    on_duplicate: str
    follow_symlinks: bool
    aliases: dict
    bvals_glob: Optional[str]


@dataclass(frozen=True)
class Inputs:
    quad_root: Optional[Path]
    participants: Optional[Path]
    id_column: str
    exclusions: Optional[Path]
    supplementary: tuple[dict, ...]


@dataclass(frozen=True)
class Imaging:
    root: Optional[Path]
    jobs: int
    patterns: dict


@dataclass(frozen=True)
class Config:
    inputs: Inputs
    discovery: Discovery
    convergence_min_domains: int
    min_relative_n: int
    rules: tuple[Rule, ...]
    fallback: Fallback
    scoring: Scoring
    imaging: Imaging
    source: Optional[Path] = None
    resolved: dict = field(default_factory=dict)


def default_config_text() -> str:
    return resources.files("quadqc").joinpath("default_config.yaml").read_text()


def _choice(value: Any, allowed: tuple, name: str) -> None:
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {', '.join(allowed)}; got {value!r}")


def _check_keys(section: dict, schema: dict, where: str) -> None:
    for key in section:
        if key not in schema:
            hint = difflib.get_close_matches(key, list(schema), n=1)
            tip = f" (did you mean '{hint[0]}'?)" if hint else ""
            raise ConfigError(f"unknown key '{key}' in {where}{tip}")


def _merge(base: dict, over: dict, where: str = "config") -> dict:
    """Overlay `over` on `base`, rejecting unknown keys. Lists replace; dicts merge."""
    _check_keys(over, base, where)
    out = copy.deepcopy(base)
    for key, value in over.items():
        # Free-form sections are replaced wholesale.
        free_form = key in ("families", "aliases")
        if isinstance(out[key], dict) and isinstance(value, dict) and not free_form:
            out[key] = _merge(out[key], value, f"{where}.{key}")
        else:
            out[key] = value
    return out


def _rule(raw: dict, where: str) -> Rule:
    if not isinstance(raw, dict) or "metric" not in raw or "domain" not in raw:
        raise ConfigError(f"{where}: each rule needs 'metric' and 'domain'; got {raw!r}")
    names = {f.name for f in fields(Rule)}
    _check_keys(raw, {n: None for n in names}, f"{where} '{raw['metric']}'")
    return Rule(**raw)


def _path(value: Optional[str], base: Path) -> Optional[Path]:
    if value in (None, ""):
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _scoring(raw: dict) -> Scoring:
    families = []
    for name, body in (raw["families"] or {}).items():
        _check_keys(body, {"label": 0, "slots": 0}, f"scoring.families.{name}")
        slots = []
        for slot in body.get("slots") or []:
            _check_keys(slot, {"name": 0, "metric": 0, "direction": 0, "substitute": 0},
                        f"scoring.families.{name}.slots")
            sub = slot.get("substitute")
            direction = slot.get("direction", "high")
            _choice(direction, ("high", "low", "both"), f"slot '{slot.get('name')}': direction")
            slots.append(Slot(slot["name"], slot["metric"], direction,
                              Substitute(**sub) if sub else None))
        if not slots:
            raise ConfigError(f"scoring family '{name}' has no slots")
        families.append(Family(name, body.get("label", name), tuple(slots)))
    known = {f.name for f in families}
    unknown = [c for c in raw["core_families"] or [] if c not in known]
    if unknown:
        raise ConfigError(f"scoring.core_families names unknown families: {unknown}")
    if not 0 < raw["min_slot_fraction"] <= 1:
        raise ConfigError("scoring.min_slot_fraction must be in (0, 1]")
    return Scoring(
        enabled=bool(raw["enabled"]), winsor=float(raw["winsor"]),
        min_slot_fraction=float(raw["min_slot_fraction"]), min_slots=int(raw["min_slots"]),
        min_families=int(raw["min_families"]), core_families=tuple(raw["core_families"] or ()),
        min_descriptive_families=int(raw["min_descriptive_families"]),
        families=tuple(families))


def from_dict(user: dict, base_dir: Path, source: Optional[Path] = None) -> Config:
    """Build a validated Config from a user mapping overlaid on the defaults."""
    defaults = yaml.safe_load(default_config_text())
    merged = _merge(defaults, user or {})
    if merged["version"] != 1:
        raise ConfigError(f"unsupported config version: {merged['version']!r}")

    disc = merged["discovery"]
    _choice(disc["on_duplicate"], ("error", "first"), "discovery.on_duplicate")
    try:
        pattern = re.compile(disc["id_regex"])
    except re.error as exc:
        raise ConfigError(f"discovery.id_regex is not a valid regex: {exc}") from exc
    if "participant" not in pattern.groupindex:
        raise ConfigError("discovery.id_regex needs a named group 'participant'")

    raw_in = merged["inputs"]
    supplementary = []
    for item in raw_in["supplementary"] or []:
        if not isinstance(item, dict) or "path" not in item:
            raise ConfigError(f"inputs.supplementary entries need 'path'; got {item!r}")
        _check_keys(item, {"path": 0, "id_column": 0}, "inputs.supplementary")
        supplementary.append({"path": _path(item["path"], base_dir),
                              "id_column": item.get("id_column", "participant_id")})
    inputs = Inputs(_path(raw_in["quad_root"], base_dir), _path(raw_in["participants"], base_dir),
                    raw_in["id_column"], _path(raw_in["exclusions"], base_dir),
                    tuple(supplementary))

    dec = merged["decision"]
    fb = merged["fallback"]
    fallback = Fallback(bool(fb["enabled"]), tuple(fb["required_metrics"] or ()),
                        tuple(_rule(r, "fallback.rules") for r in fb["rules"] or []))
    img = dict(merged["imaging"])
    root, jobs = img.pop("root"), int(img.pop("jobs"))

    return Config(
        inputs=inputs,
        discovery=Discovery(disc["id_regex"], tuple(disc["exclude"] or ()),
                            disc["on_duplicate"], bool(disc["follow_symlinks"]),
                            dict(disc["aliases"] or {}), disc["bvals_glob"]),
        convergence_min_domains=int(dec["convergence_min_domains"]),
        min_relative_n=int(dec["min_relative_n"]),
        rules=tuple(_rule(r, "rules") for r in merged["rules"] or []),
        fallback=fallback,
        scoring=_scoring(merged["scoring"]),
        imaging=Imaging(_path(root, base_dir), jobs, img),
        source=source,
        resolved=merged,
    )


def load(path: Optional[Path] = None, overrides: Optional[dict] = None) -> Config:
    """Load a YAML config (or the defaults), then apply `inputs` overrides."""
    user: dict = {}
    base_dir = Path.cwd()
    if path is not None:
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            user = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(user, dict):
            raise ConfigError(f"{path} must contain a mapping at the top level")
        base_dir = path.resolve().parent
    cfg = from_dict(user, base_dir, path)
    if overrides:
        given = {k: _path(v, Path.cwd()) for k, v in overrides.items() if v is not None}
        if given:
            inputs = {f.name: getattr(cfg.inputs, f.name) for f in fields(Inputs)}
            inputs.update(given)
            resolved = copy.deepcopy(cfg.resolved)
            resolved["inputs"].update({k: str(v) for k, v in given.items()})
            cfg = Config(**{**{f.name: getattr(cfg, f.name) for f in fields(Config)},
                            "inputs": Inputs(**inputs), "resolved": resolved})
    return cfg
