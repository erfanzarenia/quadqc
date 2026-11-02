import pytest
from conftest import write_quad

from quadqc import config, discovery
from quadqc.config import ConfigError


def _cfg(root, participants=None, **disc):
    data = {"inputs": {"quad_root": str(root)}, "discovery": disc}
    if participants:
        data["inputs"]["participants"] = str(participants)
    return config.from_dict(data, root)


def _rows(found):
    return {r.key: r for r in found.records}


def test_bids_sessions(tmp_path):
    write_quad(tmp_path, "sub-01/ses-a/dwi/eddy.qc")
    write_quad(tmp_path, "sub-01/ses-b/dwi/eddy.qc")
    (tmp_path / "sub-02/ses-a").mkdir(parents=True)
    rows = _rows(discovery.discover(_cfg(tmp_path)))
    assert set(rows) == {("sub-01", "ses-a"), ("sub-01", "ses-b"), ("sub-02", "ses-a")}
    assert rows[("sub-02", "ses-a")].state == "absent"


def test_participants_table_finds_people_without_directories(quad_tree):
    root, participants = quad_tree
    rows = _rows(discovery.discover(_cfg(root, participants)))
    assert rows[("sub-95", "")].state == "absent"
    assert rows[("sub-95", "")].in_participants_table is True
    assert rows[("sub-91", "")].state == "failed"
    assert rows[("sub-93", "")].state == "absent"


def test_participant_outside_table_is_kept_and_warned(tmp_path):
    write_quad(tmp_path, "sub-01/qc")
    table = tmp_path / "p.tsv"
    table.write_text("participant_id\nsub-02\n")
    found = discovery.discover(_cfg(tmp_path / "", table))
    rows = _rows(found)
    assert rows[("sub-01", "")].in_participants_table is False
    assert any("not in the participants table" in w for w in found.warnings)


def test_no_participants_table_is_warned(tmp_path):
    write_quad(tmp_path, "sub-01/qc")
    assert any("cannot be detected" in w for w in discovery.discover(_cfg(tmp_path)).warnings)


def test_bare_ids_in_table_are_normalised(tmp_path):
    write_quad(tmp_path, "sub-01/qc")
    table = tmp_path / "p.csv"
    table.write_text("participant_id,age\n01,30\n")
    rows = _rows(discovery.discover(_cfg(tmp_path, table)))
    assert rows[("sub-01", "")].in_participants_table is True


def test_duplicates_error_by_default(tmp_path):
    write_quad(tmp_path, "a/sub-01/qc")
    write_quad(tmp_path, "b/sub-01/qc")
    with pytest.raises(ConfigError, match="2 QUAD outputs"):
        discovery.discover(_cfg(tmp_path))


def test_duplicates_first_is_deterministic_and_logged(tmp_path):
    write_quad(tmp_path, "b/sub-01/qc")
    write_quad(tmp_path, "a/sub-01/qc")
    rec = _rows(discovery.discover(_cfg(tmp_path, on_duplicate="first")))[("sub-01", "")]
    assert rec.quad_dir == tmp_path / "a/sub-01/qc"
    assert any("2 QUAD outputs" in n for n in rec.notes)


def test_exclude_patterns_prune(tmp_path):
    write_quad(tmp_path, "batch/sub-01/qc")
    write_quad(tmp_path, "excluded/sub-02 (qc failed)/qc")
    rows = _rows(discovery.discover(_cfg(tmp_path, exclude=["*/excluded/*"])))
    assert set(rows) == {("sub-01", "")}


def test_unidentifiable_qc_json_is_an_error(tmp_path):
    write_quad(tmp_path, "scan7/qc")
    with pytest.raises(ConfigError, match="no participant ID"):
        discovery.discover(_cfg(tmp_path))


def test_custom_regex_for_non_bids_layouts(tmp_path):
    write_quad(tmp_path, "subj07/eddy.qc")
    regex = r"(?P<participant>subj\d+)"
    rows = _rows(discovery.discover(_cfg(tmp_path, id_regex=regex)))
    assert ("sub-subj07", "") in rows


def test_aliases_merge_labels(tmp_path):
    write_quad(tmp_path, "sub-OLD1/qc")
    rows = _rows(discovery.discover(_cfg(tmp_path, aliases={"sub-OLD1": "sub-1"})))
    assert ("sub-1", "") in rows


def test_missing_root_is_clear(tmp_path):
    with pytest.raises(ConfigError, match="not a directory"):
        discovery.discover(_cfg(tmp_path / "nope"))
