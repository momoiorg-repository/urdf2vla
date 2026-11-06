"""configure_task と TaskConfig のテスト（Isaac Sim 不要）: 物体の候補、置き場所（トレイ）、指示文、旧形式の task.yaml。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from test_inspect import FRANKA, make_project, run
from urdf2vla.objects import DEFAULT_OBJECTS, OBJECTS
from urdf2vla.schema.task import TaskConfig


def _configure(tmp_path, capsys, task: dict | None = None) -> tuple[dict, dict]:
    proj = make_project(tmp_path, capsys, FRANKA)
    if task is not None:
        cfg = yaml.safe_load((proj / "project.yaml").read_text())
        cfg["task"] = task
        (proj / "project.yaml").write_text(yaml.safe_dump(cfg))
    for skill in ("inspect_robot", "configure_robot"):
        run(capsys, skill, "--project", str(proj))
    r = run(capsys, "configure_task", "--project", str(proj))
    path = proj / "generated/task.yaml"
    return r, (yaml.safe_load(path.read_text()) if path.exists() else {})


def test_default_task_is_objects_into_a_tray(tmp_path, capsys):
    r, task = _configure(tmp_path, capsys)
    assert r["status"] == "ok"
    t = TaskConfig.model_validate(task)
    # Franka の指の開き 8 cm に、既定の物体はすべて入る
    assert r["data"]["gripper_opening_m"] == 0.08
    assert [o.key for o in t.objects] == list(DEFAULT_OBJECTS)
    assert t.target.is_container and t.target.key == "klt_bin"
    assert t.instruction_for(0) == "put the apple in the bin"
    assert t.table.material == "oak"
    # トレイは物体を置く範囲と重ならず、台の上に収まる
    tray_left = t.target.pos[1] + t.target.size[1] / 2
    assert tray_left + max(o.grasp_width for o in t.objects) / 2 < t.spawn_y[0]
    assert abs(t.target.pos[1] - t.table.center[1]) + t.target.size[1] / 2 <= t.table.size[1] / 2
    # りんごは縮小して使い、幅がカタログの値 × 縮尺
    apple = next(o for o in t.objects if o.key == "apple")
    assert apple.scale < 1 and abs(apple.size[0] - OBJECTS["apple"].size[0] * apple.scale) < 1e-3


def test_primitive_cube_and_disc(tmp_path, capsys):
    r, task = _configure(tmp_path, capsys, {"objects": ["cube"], "container": "disc"})
    t = TaskConfig.model_validate(task)
    assert [o.key for o in t.objects] == ["cube"] and t.objects[0].asset is None
    assert not t.target.is_container
    assert t.language_instruction == "pick up the red cube and place it on the green target"


def test_unknown_object_fails(tmp_path, capsys):
    r, _ = _configure(tmp_path, capsys, {"objects": ["teapot"]})
    assert r["status"] == "error" and "teapot" in r["errors"][0]


def test_old_single_object_task_yaml_still_loads():
    old = {
        "table": {"center": [0.5, 0.0], "size": [0.7, 1.1, 0.05], "top_z": 0.0},
        "object": {"name": "cube", "size": [0.045, 0.045, 0.045], "spawn_x": [0.4, 0.5], "spawn_y": [0.06, 0.16]},
        "target": {"pos": [0.5, -0.12], "radius": 0.06},
        "language_instruction": "pick up the red cube and place it on the green target",
    }
    t = TaskConfig.model_validate(old)
    assert len(t.objects) == 1 and t.objects[0].key == "cube"
    assert t.spawn_x == [0.4, 0.5] and t.language_instruction == old["language_instruction"]


# FANUC CRX-3iA（FANUC の公式の URDF, Apache-2.0）は同梱していない。examples/crx3ia/fetch_urdf.sh で取得すると動く
CRX_EXAMPLE = Path(__file__).resolve().parent.parent / "examples/crx3ia/fanuc_description/crx3ia.urdf"


@pytest.mark.skipif(not CRX_EXAMPLE.exists(), reason="run examples/crx3ia/fetch_urdf.sh to get the FANUC URDF")
@pytest.mark.parametrize("gripper", ["robotiq_2f_85", "ufactory_xarm"])
def test_crx3ia_gripper_swap_keeps_the_workspace_reachable(tmp_path, capsys, gripper):
    from urdf2vla.cli import main
    from urdf2vla.grippers import get_gripper
    from urdf2vla.project import Project
    from urdf2vla.schema.robot import RobotConfig
    from urdf2vla.urdf.parser import parse_urdf
    from urdf2vla.urdf.reach import ReachChecker

    proj = tmp_path / "crx"
    main(["init", str(proj), "--from", str(CRX_EXAMPLE.parent.parent / "project.yaml")])
    capsys.readouterr()
    cfg = yaml.safe_load((proj / "project.yaml").read_text())
    cfg["robot"]["gripper"]["simready"] = gripper
    (proj / "project.yaml").write_text(yaml.safe_dump(cfg))
    for skill in ("inspect_robot", "configure_robot"):
        assert run(capsys, skill, "--project", str(proj))["status"] == "ok"
    r = run(capsys, "configure_task", "--project", str(proj))
    assert r["status"] == "ok"
    robot = RobotConfig.model_validate(Project(proj).read_generated("robot_config.yaml"))
    task = TaskConfig.model_validate(Project(proj).read_generated("task.yaml"))
    # グリッパは FANUC のフランジ座標に、指がフランジ面から外向き（J6 の +x）に伸びる向きで付く
    assert robot.grippers[0].simready == gripper and robot.grippers[0].mount_link == "fanuc_flange"
    assert robot.arms[0].ik_body == "J6_link" and robot.arms[0].approach_axis[0] > 0.99
    assert abs(robot.arms[0].tcp_offset.pos[0] - (0.123 + get_gripper(gripper).tcp_depth)) < 0.005
    # 作業の構えは箱の縁より上。腕が短くグリッパが長いので、台は手前に寄り、作業域のすべての点に真下向きで届く
    home = task.home_tcp["main"]
    assert home[2] >= task.table.top_z + task.target.size[2] + 0.06 - 1e-6
    assert any("手前に寄せました" in w for w in r["warnings"])
    assert not any("届かない点" in w for w in r["warnings"])
    checker = ReachChecker(parse_urdf(robot.asset.host_path), robot, robot.arms[0])
    assert checker.reachable((home[0] + 0.064, home[1], home[2])) and checker.reachable((home[0], home[1], home[2] + 0.064))


@pytest.mark.skipif(not CRX_EXAMPLE.exists(), reason="run examples/crx3ia/fetch_urdf.sh to get the FANUC URDF")
@pytest.mark.parametrize("mount", [None, "fanuc_flange", "flange"])
def test_crx3ia_gripper_points_out_of_the_flange_whichever_frame(tmp_path, capsys, mount):
    """腕だけの URDF: mount_link を書かなくても、+x が外向きの flange に付けても、指はフランジ面から外へ伸びる。"""
    from urdf2vla.cli import main
    from urdf2vla.project import Project
    from urdf2vla.schema.robot import RobotConfig

    proj = tmp_path / "crx"
    main(["init", str(proj), "--from", str(CRX_EXAMPLE.parent.parent / "project.yaml")])
    capsys.readouterr()
    cfg = yaml.safe_load((proj / "project.yaml").read_text())
    cfg["robot"]["gripper"] = {"simready": "robotiq_2f_85", **({"mount_link": mount} if mount else {})}
    (proj / "project.yaml").write_text(yaml.safe_dump(cfg))
    run(capsys, "inspect_robot", "--project", str(proj))
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "ok"
    robot = RobotConfig.model_validate(Project(proj).read_generated("robot_config.yaml"))
    assert robot.grippers[0].mount_link == (mount or "fanuc_flange")  # 書かなければ +z が外向きのフレームを選ぶ
    assert robot.arms[0].approach_axis[0] > 0.99  # J6_link の +x = フランジ面から外
    assert abs(robot.arms[0].tcp_offset.pos[0] - (0.123 + 0.1484 - 0.012)) < 0.005
