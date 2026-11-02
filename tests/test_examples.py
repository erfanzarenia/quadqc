import runpy
import sys
from pathlib import Path

import pandas as pd
import pytest

from quadqc import cli, config

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.yaml")), ids=lambda p: p.name)
def test_example_configs_are_valid(path):
    assert config.load(path).inputs.quad_root is not None


def test_documented_demo(tmp_path, monkeypatch, capsys):
    data = tmp_path / "demo_data"
    monkeypatch.setattr(sys, "argv", ["make_demo_data.py", str(data)])
    runpy.run_path(str(EXAMPLES / "make_demo_data.py"), run_name="__main__")
    out = tmp_path / "demo_out"
    assert cli.main(["run", "--quad-root", str(data / "derivatives"), "--participants",
                     str(data / "participants.tsv"), "--out", str(out)]) == 0
    table = pd.read_csv(out / "decisions.tsv", sep="\t", keep_default_na=False)
    status = dict(zip(table.participant_id, table.status))
    assert status["sub-25"] == "fail"
    assert status["sub-26"] == "caveat"
    assert status["sub-27"] == "incomplete"
    assert status["sub-28"] == "fail"
    assert {status[p] for p in ("sub-29", "sub-30", "sub-31")} == {"missing"}
    assert len(status) == 31
    assert "fail" in (out / "report.md").read_text()
