"""generate_mimic のホスト側の検査（Isaac Sim 不要）: Mimic は物体 1 つ + 容器のタスクだけで使える。"""

from __future__ import annotations

import logging

from test_task import _configure
from urdf2vla.project import Project
from urdf2vla.registry import OPTIONAL, SKILLS
from urdf2vla.result import SkillResult


def _generate(proj_dir) -> SkillResult:
    import urdf2vla.skills  # noqa: F401  registry に載せる

    result = SkillResult(skill="generate_mimic")
    opts = {"source_demos": 2, "trials": 3, "dataset": "demos", "seed": 0, "timeout": 1.0}
    SKILLS["generate_mimic"].func(Project(proj_dir), opts, result, logging.getLogger("test"))
    return result


def test_generate_mimic_is_an_optional_cli_skill():
    import urdf2vla.cli  # noqa: F401

    assert "generate_mimic" in OPTIONAL and SKILLS["generate_mimic"].requires == ["setup_teleoperation"]
    assert [a.flags[0] for a in SKILLS["generate_mimic"].args][:3] == ["--source-demos", "--source-device", "--trials"]


def test_generate_mimic_needs_one_object_and_a_container(tmp_path, capsys):
    _configure(tmp_path / "many", capsys)  # 既定: 物体 4 つ
    r = _generate(tmp_path / "many" / "proj")
    assert r.status == "error" and "物体 1 つと容器" in r.errors[0]

    _configure(tmp_path / "disc", capsys, {"objects": ["cube"], "container": "disc"})
    r = _generate(tmp_path / "disc" / "proj")
    assert r.status == "error" and "物体 1 つと容器" in r.errors[0]
