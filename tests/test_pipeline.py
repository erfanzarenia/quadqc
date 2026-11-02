import filecmp
import json

import pandas as pd
import pytest

from quadqc import cli, config, pipeline
from quadqc.config import ConfigError


def _cfg(root, participants, tmp_path, **over):
    return config.load(None, {"quad_root": str(root), "participants": str(participants)})


def _status(out):
    table = pd.read_csv(out / "decisions.tsv", sep="\t", keep_default_na=False)
    return dict(zip(table.participant_id, table.status))


def test_cli_run_end_to_end(quad_tree, tmp_path):
    root, participants = quad_tree
    out = tmp_path / "out"
    code = cli.main(["run", "--quad-root", str(root), "--participants", str(participants),
                     "--out", str(out)])
    assert code == 0
    status = _status(out)
    assert status["sub-90"] == "fail"
    assert status["sub-91"] == "missing"  # eddy_quad crashed
    assert status["sub-92"] == "missing"  # corrupt json
    assert status["sub-93"] == "missing"  # directory only
    assert status["sub-95"] == "missing"  # roster only
    assert status["sub-94"] == "incomplete"  # no outlier map: never a silent pass
    assert len(status) == 30
    for name in ("inventory.tsv", "metrics.tsv", "rule_evaluations.tsv", "rule_summary.tsv",
                 "scores.tsv", "score_sources.tsv", "score_slots.tsv", "score_families.tsv",
                 "report.md", "provenance.json"):
        assert (out / name).is_file(), name


def test_quad_states_are_distinguished(quad_tree, tmp_path):
    root, participants = quad_tree
    out = tmp_path / "out"
    pipeline.run(_cfg(root, participants, tmp_path), out)
    inv = pd.read_csv(out / "inventory.tsv", sep="\t", keep_default_na=False)
    state = dict(zip(inv.participant_id, inv.quad_state))
    assert (state["sub-91"], state["sub-92"], state["sub-93"], state["sub-94"]) == (
        "failed", "invalid", "absent", "partial")


def test_outputs_are_byte_reproducible(quad_tree, tmp_path):
    root, participants = quad_tree
    pipeline.run(_cfg(root, participants, tmp_path), tmp_path / "a")
    pipeline.run(_cfg(root, participants, tmp_path), tmp_path / "b")
    names = [p.name for p in (tmp_path / "a").iterdir() if p.name != "provenance.json"]
    match, mismatch, errors = filecmp.cmpfiles(tmp_path / "a", tmp_path / "b", names, shallow=False)
    assert sorted(match) == sorted(names) and not mismatch and not errors


def test_inputs_are_not_modified(quad_tree, tmp_path):
    root, participants = quad_tree
    def snapshot():
        return {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*")}
    before = snapshot()
    pipeline.run(_cfg(root, participants, tmp_path), tmp_path / "out")
    assert snapshot() == before


def test_refuses_output_inside_input(quad_tree):
    root, participants = quad_tree
    assert cli.main(["run", "--quad-root", str(root), "--out", str(root / "qc")]) == 2
    assert not (root / "qc").exists()


def test_refuses_to_overwrite_without_flag(quad_tree, tmp_path):
    root, participants = quad_tree
    out = tmp_path / "out"
    pipeline.run(_cfg(root, participants, tmp_path), out)
    with pytest.raises(ConfigError, match="not empty"):
        pipeline.run(_cfg(root, participants, tmp_path), out)
    pipeline.run(_cfg(root, participants, tmp_path), out, overwrite=True)


def test_provenance_records_inputs_and_config(quad_tree, tmp_path):
    root, participants = quad_tree
    out = tmp_path / "out"
    pipeline.run(_cfg(root, participants, tmp_path), out)
    prov = json.loads((out / "provenance.json").read_text())
    assert prov["tool"] == "quadqc"
    assert prov["config_sha256"]
    assert any(f["path"].endswith("qc.json") and len(f["sha256"]) == 64
               for f in prov["input_files"])
    assert prov["config"]["fallback"]["enabled"] is False


def test_supplementary_metrics_and_rules(quad_tree, tmp_path):
    root, participants = quad_tree
    extra = tmp_path / "extra.tsv"
    extra.write_text("participant_id\treg_dice\tnote\nsub-01\t0.5\tx\nsub-02\t0.97\ty\n")
    path = tmp_path / "c.yaml"
    path.write_text(
        "inputs:\n"
        f"  quad_root: {root}\n"
        "  supplementary:\n  - {path: extra.tsv}\n"
        "rules:\n  - {metric: reg_dice, domain: reg, direction: low, severe: 0.9, required: false,"
        " pathway: all}\n")
    result = pipeline.run(config.load(path), tmp_path / "out")
    status = dict(zip(result["decisions"].decisions.participant_id,
                      result["decisions"].decisions.status))
    assert status["sub-01"] == "fail" and status["sub-02"] == "pass"
    assert any("non-numeric" in w for w in result["warnings"])


def test_supplementary_collision_is_an_error(quad_tree, tmp_path):
    root, _ = quad_tree
    extra = tmp_path / "extra.tsv"
    extra.write_text("participant_id\teddy_snr_b0\nsub-01\t1\n")
    path = tmp_path / "c.yaml"
    path.write_text(f"inputs:\n  quad_root: {root}\n  supplementary:\n  - {{path: extra.tsv}}\n")
    with pytest.raises(ConfigError, match="already defined"):
        pipeline.build_cohort(config.load(path))


def test_exclusions_need_reasons_and_stay_reported(quad_tree, tmp_path):
    root, _ = quad_tree
    table = tmp_path / "ex.tsv"
    path = tmp_path / "c.yaml"
    path.write_text(f"inputs:\n  quad_root: {root}\n  exclusions: ex.tsv\n")
    table.write_text("participant_id\treason\nsub-01\t\n")
    with pytest.raises(ConfigError, match="needs a reason"):
        pipeline.build_cohort(config.load(path))
    table.write_text("participant_id\treason\nsub-01\tscanner fault\n")
    result = pipeline.run(config.load(path), tmp_path / "out")
    row = result["decisions"].decisions.set_index("participant_id").loc["sub-01"]
    assert (row.status, row.exclusion_reason) == ("excluded", "scanner fault")


def test_validate_reports_and_writes_nothing(quad_tree, tmp_path, capsys):
    root, participants = quad_tree
    assert cli.main(["validate", "--quad-root", str(root), "--participants",
                     str(participants)]) == 0
    captured = capsys.readouterr().out
    assert "failed: 1" in captured and "invalid: 1" in captured
    assert cli.main(["validate", "--quad-root", str(root), "--participants",
                     str(participants), "--strict"]) == 1


def test_mixed_protocols_are_warned(tmp_path):
    from conftest import make_qc, write_quad
    write_quad(tmp_path, "sub-01/qc", make_qc())
    write_quad(tmp_path, "sub-02/qc", make_qc(data_unique_bvals=[700], qc_cnr_avg=[20.0, 1.0],
                                              qc_outliers_b=[0.5]))
    cohort = pipeline.build_cohort(config.load(None, {"quad_root": str(tmp_path)}))
    assert any("acquisition protocols" in w for w in cohort.warnings)


def test_identity_mismatch_is_warned(tmp_path):
    from conftest import make_qc, write_quad
    write_quad(tmp_path, "sub-01/qc", make_qc(qc_path="out/sub-99/qc"))
    cohort = pipeline.build_cohort(config.load(None, {"quad_root": str(tmp_path)}))
    assert any("names sub-99" in w for w in cohort.warnings)


def test_wildcard_with_no_match_is_warned(tmp_path):
    from conftest import write_quad
    write_quad(tmp_path / "d", "sub-01/qc")
    path = tmp_path / "c.yaml"
    path.write_text("inputs: {quad_root: d}\nrules:\n"
                    "  - {metric: 'nope*', kind: relative, domain: x, z: 3}\n")
    assert any("matches no metric" in w for w in pipeline.build_cohort(config.load(path)).warnings)


def test_bad_glob_placeholder_is_a_clear_error(tmp_path):
    from conftest import write_quad
    write_quad(tmp_path / "d", "sub-01/qc")
    path = tmp_path / "c.yaml"
    path.write_text("inputs: {quad_root: d}\ndiscovery: {bvals_glob: '{subject}/x.bval'}\n")
    with pytest.raises(ConfigError, match="bad placeholder"):
        pipeline.build_cohort(config.load(path))


def test_init_config_round_trip(tmp_path):
    path = tmp_path / "c.yaml"
    assert cli.main(["init-config", "--out", str(path)]) == 0
    assert config.load(path).scoring.enabled
    assert cli.main(["init-config", "--out", str(path)]) == 2


def test_cli_reports_clean_errors(tmp_path, capsys):
    assert cli.main(["validate", "--quad-root", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err
