"""双腕ロボットのホスト側フロー: 左右の腕・グリッパ・作業域・カメラ・VLA の次元。"""

from __future__ import annotations

import pytest
import yaml

from test_analysis_generic import side_by_side_bimanual
from test_inspect import make_project, run
from urdf2vla.gr00t.modality import modality_json
from urdf2vla.schema.camera import CamerasConfig
from urdf2vla.schema.robot import RobotConfig
from urdf2vla.schema.task import TaskConfig
from urdf2vla.skills.robot import _control_joints, _hint
from urdf2vla.skills.vla import build_vla_interface


def test_dual_arm_hints_can_come_from_project_yaml():
    robot = {
        "front_axis": "+y",
        "gripper_open_close": [[-0.8, 0.0], [-0.7, 0.0]],
    }
    assert _hint(robot, {}, "robot.front_axis") == "+y"
    assert _hint(robot, {}, "grippers.0.open_close") == [-0.8, 0.0]
    assert _hint(robot, {}, "grippers.1.open_close") == [-0.7, 0.0]
    joints = {"left": ["l1", "l2"], "right": ["r1", "r2"]}
    assert _control_joints({"control_joints": joints}, "left", 2) == ["l1", "l2"]
    assert _control_joints({"control_joints": ["l1", "l2"]}, "left", 1) == ["l1", "l2"]


def test_dual_arm_setup_keeps_both_arms_and_grippers(tmp_path, capsys):
    urdf = side_by_side_bimanual(tmp_path / "bimanual.urdf")
    proj = make_project(
        tmp_path,
        capsys,
        urdf,
        {
            "gripper_joints": ["joint9_R", "joint9_L"],
            "gripper_open_position": ["lower", "lower"],
            "front_axis": "+y",
        },
    )
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "ok", r
    assert {a["name"] for a in r["data"]["arms"]} == {"right", "left"}
    assert len(r["data"]["grippers"]) == 2

    for skill in ("configure_robot", "configure_task", "configure_camera"):
        r = run(capsys, skill, "--project", str(proj))
        assert r["status"] == "ok", (skill, r)

    robot = RobotConfig.model_validate(yaml.safe_load((proj / "generated/robot_config.yaml").read_text()))
    cameras = CamerasConfig.model_validate(yaml.safe_load((proj / "generated/cameras.yaml").read_text()))
    task = TaskConfig.model_validate(yaml.safe_load((proj / "generated/task.yaml").read_text()))

    assert [a.name for a in robot.arms] == ["right", "left"]
    assert [g.arm for g in robot.grippers] == ["right", "left"]
    assert set(task.home_tcp) == {"right", "left"}
    # 双腕の作業域は左右の肩の中央。物体・箱・台が片方の腕の側だけに寄らない
    assert abs(task.table.center[1]) < 1e-6
    assert abs((sum(task.spawn_y) / 2) - task.table.center[1]) < 0.25 * task.table.size[1]
    assert task.target.pos[1] < 0.0
    assert task.table.center[1] - task.table.size[1] / 2 <= task.target.pos[1] - task.target.size[1] / 2
    assert task.target.pos[1] + task.target.size[1] / 2 <= task.table.center[1] + task.table.size[1] / 2

    assert {c.name for c in cameras.cameras} == {"right_wrist_cam", "left_wrist_cam", "left_cam", "right_cam"}

    vla = build_vla_interface(robot, cameras, task)
    assert [g.name for g in vla.state.groups] == ["right_arm", "left_arm", "right_gripper", "left_gripper"]
    assert vla.state.dimension == sum(len(a.joints) for a in robot.arms) + 2
    assert vla.action.dimension == vla.state.dimension
    assert [i.key for i in vla.images] == ["right_wrist_cam", "left_wrist_cam", "left_cam", "right_cam"]
    modality = modality_json(vla)
    assert modality["state"]["left_arm"]["start"] == len(robot.arms[0].joints)
    assert modality["action"]["left_gripper"]["end"] == vla.action.dimension

    # Mimic: 手先は腕ごと、グリッパの列は腕ごとの 7 次元の後にグリッパの順（右腕が先に検出されたロボット）
    from urdf2vla.mimic_layout import eef_names, grasp_signal, gripper_column, pick_working_arm

    assert eef_names(robot) == [a.name for a in robot.arms]
    n = 7 * len(robot.arms)
    assert [gripper_column(robot, a.name) for a in robot.arms] == [n + robot.grippers.index(robot.gripper_for(a.name)) for a in robot.arms]
    assert sorted(gripper_column(robot, a.name) for a in robot.arms) == [n, n + 1]
    assert grasp_signal(robot, "left") == "grasp_left"
    # 作業する腕: どのデモも同じ 1 本の腕で掴んでいること
    assert pick_working_arm({"demo_0": ["left"], "demo_1": ["left"]}) == "left"
    for bad in ({"demo_0": ["left"], "demo_1": ["right"]}, {"demo_0": []}, {"demo_0": ["left", "right"]}):
        with pytest.raises(ValueError):
            pick_working_arm(bad)
