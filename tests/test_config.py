import pytest

from quadqc import config
from quadqc.config import ConfigError, Rule


def test_defaults_load():
    cfg = config.load()
    assert cfg.convergence_min_domains == 3
    assert cfg.fallback.enabled is False
    assert {f.name for f in cfg.scoring.families} == {"motion", "signal", "correction"}


def test_unknown_key_suggests_correction(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("discovery:\n  excludes: []\n")
    with pytest.raises(ConfigError, match="did you mean 'exclude'"):
        config.load(path)


def test_paths_resolve_against_config_directory(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("inputs:\n  quad_root: data\n")
    assert config.load(path).inputs.quad_root == tmp_path / "data"


def test_overrides_are_recorded(tmp_path):
    cfg = config.load(None, {"quad_root": str(tmp_path)})
    assert cfg.inputs.quad_root == tmp_path
    assert cfg.resolved["inputs"]["quad_root"] == str(tmp_path)


@pytest.mark.parametrize("kwargs,message", [
    (dict(review=5, severe=1), "less extreme"),
    (dict(direction="low", review=1, severe=5), "less extreme"),
    (dict(), "review and/or severe"),
    (dict(kind="relative"), "positive z"),
    (dict(kind="relative", z=3, review=1), "cannot set review"),
    (dict(direction="both", review=1), "only valid for relative"),
    (dict(direction="sideways", review=1), "direction"),
])
def test_invalid_rules(kwargs, message):
    with pytest.raises(ConfigError, match=message):
        Rule(metric="m", domain="d", **kwargs)


def test_regex_needs_participant_group(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("discovery:\n  id_regex: 'sub-.*'\n")
    with pytest.raises(ConfigError, match="named group"):
        config.load(path)


def test_lists_replace_defaults(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("rules:\n  - {metric: x, domain: d, review: 1}\n")
    assert [r.metric for r in config.load(path).rules] == ["x"]
