"""inspect_robot / configure_robot のホスト側テスト（Isaac Sim 不要）。"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest
import yaml

from urdf2vla.cli import main
from urdf2vla.urdf.kinematics import Transform, look_at_ros, quat_rotate

ROOT = Path(__file__).resolve().parents[1]
FRANKA = ROOT / "examples/franka/franka_description/robots/panda_arm_hand.urdf"
# FANUC CRX-3iA の URDF（メッシュは URDF の外にある）は同梱していない。持っていれば URDF2VLA_CRX_URDF で指定すると
# その URDF での試験も実行する（なければ飛ばす）
CRX = Path(os.environ["URDF2VLA_CRX_URDF"]) if os.environ.get("URDF2VLA_CRX_URDF") else None
HAS_CRX = CRX is not None and CRX.is_file()


def run(capsys, *argv) -> dict:
    main(list(argv))
    return json.loads(capsys.readouterr().out)


def make_project(tmp_path: Path, capsys, urdf: Path, extra_robot: dict | None = None) -> Path:
    proj = tmp_path / "proj"
    run(capsys, "init", str(proj), "--urdf", str(urdf))
    if extra_robot:
        cfg = yaml.safe_load((proj / "project.yaml").read_text())
        cfg["robot"].update(extra_robot)
        (proj / "project.yaml").write_text(yaml.safe_dump(cfg))
    return proj


def _link(name, mass=1.0):
    return f'<link name="{name}"><inertial><mass value="{mass}"/><inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/></inertial></link>'


def _joint(name, jtype, parent, child, xyz="0 0 0.1", lo=-1.0, hi=1.0, extra=""):
    limit = f'<limit lower="{lo}" upper="{hi}" effort="10" velocity="1"/>' if jtype in ("revolute", "prismatic") else ""
    return f'<joint name="{name}" type="{jtype}"><parent link="{parent}"/><child link="{child}"/><origin xyz="{xyz}" rpy="0 0 0"/><axis xyz="0 0 1"/>{limit}{extra}</joint>'


def bimanual_urdf(path: Path) -> Path:
    parts = [_link("base_link", 10.0), _link("torso")]
    parts.append(_joint("lift_joint", "prismatic", "base_link", "torso", lo=-0.3, hi=0.0))
    for w in ("fl", "fr", "rl", "rr"):
        parts += [_link(f"{w}_wheel"), _joint(f"{w}_wheel_joint", "continuous", "base_link", f"{w}_wheel")]
    parts += [_link("head"), _joint("head_pan", "revolute", "torso", "head")]
    for side in ("left", "right"):
        prev = "torso"
        for i in range(1, 7):
            link = f"{side}_link{i}"
            parts += [_link(link), _joint(f"{side}_joint{i}", "revolute", prev, link)]
            prev = link
        parts += [_link(f"{side}_end_effector_link", 0.3), _joint(f"{side}_joint7", "revolute", prev, f"{side}_end_effector_link")]
        for f in ("a", "b"):  # mimic なしの独立した指
            parts += [_link(f"{side}_rh_{f}", 0.05), _joint(f"{side}_rh_{f}", "revolute", f"{side}_end_effector_link", f"{side}_rh_{f}", xyz="0 0.02 0.05", lo=0.0, hi=1.1)]
    path.write_text(f'<robot name="bimanual">{"".join(parts)}</robot>')
    return path


# --------------------------------------------------------------------------- tests


def test_franka_needs_no_questions(tmp_path, capsys):
    proj = make_project(tmp_path, capsys, FRANKA)
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "ok", r
    inferred = {i["key"]: i["value"] for i in r["inferred"]}
    assert inferred["arms.0.ee_link"] == "panda_hand"
    assert inferred["grippers.0.open_position"] == "upper"
    assert r["data"]["grippers"][0]["mimic"] == ["panda_finger_joint2"]
    assert r["data"]["meshes"]["unresolved"] == []


def test_franka_configure_chain(tmp_path, capsys):
    proj = make_project(tmp_path, capsys, FRANKA)
    for skill in ("inspect_robot", "configure_robot", "configure_task", "configure_camera"):
        r = run(capsys, skill, "--project", str(proj))
        assert r["status"] == "ok", (skill, r)
    cfg = yaml.safe_load((proj / "generated/robot_config.yaml").read_text())
    g = cfg["grippers"][0]
    assert g["open"] == {"panda_finger_joint1": 0.04, "panda_finger_joint2": 0.04}
    assert g["close"] == {"panda_finger_joint1": 0.0, "panda_finger_joint2": 0.0}
    assert cfg["arms"][0]["ik_body"] == "panda_hand"
    assert 0.08 < cfg["arms"][0]["tcp_offset"]["pos"][2] < 0.13
    assert cfg["provenance"]["default_pose"] == "default"  # 初期姿勢を書いていないので中央値 + 警告
    # 正規化 URDF はコンテナ側パスのメッシュを持つ
    urdf = (proj / "build/robot.urdf").read_text()
    assert "package://" not in urdf and "/workspace/urdf2vla/" in urdf
    cams = yaml.safe_load((proj / "generated/cameras.yaml").read_text())["cameras"]
    # 既定は DROID と同じ配置: 手先 + ロボットの左右の外部カメラ
    assert [c["name"] for c in cams] == ["wrist_cam", "left_cam", "right_cam"]
    left, right = cams[1], cams[2]
    assert left["pos"][1] > 0.3 and abs(left["pos"][1] + right["pos"][1]) < 1e-6  # 左右対称（ロボットの左 = +y）
    assert left["pos"][0] == right["pos"][0] and left["pos"][2] == right["pos"][2] and left["pos"][2] > 0.4
    task = yaml.safe_load((proj / "generated/task.yaml").read_text())
    from urdf2vla.urdf.kinematics import quat_rotate

    for cam in (left, right):  # 光軸（ROS の +z）が台の中央の方を向く
        fwd = quat_rotate(tuple(cam["rot_xyzw"]), (0.0, 0.0, 1.0))
        to_table = [task["table"]["center"][0] - cam["pos"][0], task["table"]["center"][1] - cam["pos"][1], task["table"]["top_z"] - cam["pos"][2]]
        n = sum(c * c for c in to_table) ** 0.5
        assert sum(a * b / n for a, b in zip(fwd, to_table)) > 0.95


def test_requires_previous_step(tmp_path, capsys):
    proj = make_project(tmp_path, capsys, FRANKA)
    r = run(capsys, "configure_task", "--project", str(proj))
    assert r["status"] == "error" and "configure_robot" in r["errors"][0]


@pytest.mark.skipif(not HAS_CRX, reason="set URDF2VLA_CRX_URDF to a CRX-3iA URDF to run")
def test_crx_asks_ee_and_gripper_direction(tmp_path, capsys):
    proj = make_project(tmp_path, capsys, CRX)
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "needs_input"
    qs = {q["key"]: q for q in r["questions"]}
    assert set(qs["arms.0.ee_link"]["candidates"]) == {"robotiq_85_base_link", "end_effector"}
    assert qs["grippers.0.open_position"]["candidates"] == ["lower", "upper"]
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "needs_input"  # 回答なしでは進まない
    run(capsys, "answer", "--project", str(proj), "arms.0.ee_link=end_effector", "grippers.0.open_position=lower")
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "ok", r
    cfg = yaml.safe_load((proj / "generated/robot_config.yaml").read_text())
    arm = cfg["arms"][0]
    # end_effector は質量のない frame → 剛体の祖先 J6_link に取り付ける
    assert arm["ee_link"] == "end_effector" and arm["ik_body"] == "J6_link"
    assert cfg["root_link"] == "base_link"  # 空の world link を外した
    assert cfg["grippers"][0]["close"]["robotiq_85_right_knuckle_joint"] == -0.8  # mimic multiplier -1
    # mimic の拘束は使わず、follower も含めて全関節を駆動する（multiplier -1 の拘束は PhysX で発散するため）
    g = cfg["grippers"][0]
    act = next(a for a in cfg["actuators"] if a["name"] == g["name"])
    assert g["leader"] in act["joints"] and set(g["mimic"]) <= set(act["joints"])
    assert not any(a["name"].endswith("_mimic") for a in cfg["actuators"])
    # コンテナから見えない場所のメッシュは project の build/meshes/ にコピーして参照する
    urdf_text = (proj / "build/robot.urdf").read_text()
    assert str(CRX.parent) not in urdf_text and "/tmpproj/proj/build/meshes/" in urdf_text


def test_bimanual_with_extra_joints(tmp_path, capsys):
    urdf = bimanual_urdf(tmp_path / "bimanual.urdf")
    proj = make_project(tmp_path, capsys, urdf)
    r = run(capsys, "inspect_robot", "--project", str(proj))
    arms = {a["name"]: a for a in r["data"]["arms"]}
    assert set(arms) == {"left", "right"}
    assert arms["left"]["joints"] == [f"left_joint{i}" for i in range(1, 8)]
    assert len(r["data"]["grippers"]) == 2
    # 車輪は gripper と誤認しない。昇降・首・車輪は役割を質問する
    assert set(r["data"]["other_joints"]) == {"lift_joint", "head_pan", "fl_wheel_joint", "fr_wheel_joint", "rl_wheel_joint", "rr_wheel_joint"}
    qs = {q["key"]: q for q in r["questions"]}
    assert qs["joints.fl_wheel_joint.role"]["suggestion"] == "passive"
    assert qs["joints.lift_joint.role"]["suggestion"] == "fixed_at_reset"
    assert "grippers.0.open_position" in qs  # 回転関節の指は開く向きを質問
    answers = [f"joints.{n}.role={qs[f'joints.{n}.role']['suggestion']}" for n in r["data"]["other_joints"]]
    run(capsys, "answer", "--project", str(proj), *answers, "grippers.0.open_position=lower", "grippers.1.open_position=lower")
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "ok", r
    cfg = yaml.safe_load((proj / "generated/robot_config.yaml").read_text())
    assert cfg["joint_roles"]["passive"] == ["fl_wheel_joint", "fr_wheel_joint", "rl_wheel_joint", "rr_wheel_joint"]
    assert [a["ee_link"] for a in cfg["arms"]] == ["left_end_effector_link", "right_end_effector_link"]
    assert {g["arm"] for g in cfg["grippers"]} == {"left", "right"}


def test_unresolved_mesh_is_error(tmp_path, capsys):
    urdf = tmp_path / "bad.urdf"
    urdf.write_text(
        textwrap.dedent(
            """\
            <robot name="bad">
              <link name="base"><visual><geometry><mesh filename="package://nowhere_pkg/meshes/a.stl"/></geometry></visual></link>
              <link name="l1"><inertial><mass value="1"/></inertial></link>
              <joint name="j1" type="revolute"><parent link="base"/><child link="l1"/><limit lower="-1" upper="1" effort="1" velocity="1"/></joint>
            </robot>"""
        )
    )
    proj = make_project(tmp_path, capsys, urdf)
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "error"
    assert "package_paths" in r["errors"][0]


def test_look_at_points_camera_z_to_target():
    eye, target = (1.0, 0.5, 0.8), (0.3, -0.1, 0.0)
    q = look_at_ros(eye, target)
    z = quat_rotate(q, (0.0, 0.0, 1.0))
    d = [t - e for t, e in zip(target, eye)]
    n = sum(c * c for c in d) ** 0.5
    assert all(abs(a - b / n) < 1e-6 for a, b in zip(z, d))
    # 画像の下方向（+Y）は概ね world の -Z を向く
    assert quat_rotate(q, (0.0, 1.0, 0.0))[2] < 0


def test_transform_inverse_roundtrip():
    t = Transform.from_xyz_rpy((0.1, -0.2, 0.3), (0.4, -0.5, 0.6))
    i = t.inverse() @ t
    assert all(abs(c) < 1e-9 for c in i.pos)
    assert abs(abs(i.rot[3]) - 1.0) < 1e-9  # (x, y, z, w) の w


@pytest.mark.skipif(not HAS_CRX, reason="set URDF2VLA_CRX_URDF to a CRX-3iA URDF to run")
def test_crx_simready_gripper_replaces_urdf_gripper(tmp_path, capsys):
    """robot.gripper.simready: URDF のグリッパを外し、SimReady の Robotiq 2F-85 を同じ位置・向きで付ける設定になる。"""
    proj = make_project(tmp_path, capsys, CRX, {"gripper": {"simready": "robotiq_2f_85"}})
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "ok", r  # グリッパと End Effector の質問は出ない
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "ok", r
    cfg = yaml.safe_load((proj / "generated/robot_config.yaml").read_text())
    g = cfg["grippers"][0]
    assert g["simready"] == "robotiq_2f_85" and g["joints"] == ["finger_joint"] and g["mount_link"] == "flange"
    assert g["open"] == {"finger_joint": 0.0} and g["close"] == {"finger_joint": 0.785}
    arm = cfg["arms"][0]
    assert arm["ee_link"] == "flange" and arm["ik_body"] == "J6_link"
    # TCP は URDF のグリッパのときとほぼ同じ所（J6_link の x 方向 約 0.26 m）。指は同じ向きに開く
    assert abs(arm["tcp_offset"]["pos"][0] - 0.2594) < 1e-3 and abs(arm["finger_axis"][2]) > 0.99
    groups = {a["name"]: a for a in cfg["actuators"]}
    assert groups["main_gripper"]["joints"] == ["finger_joint"]
    assert groups["main_gripper_mimic"]["stiffness"] == 0.0 and len(groups["main_gripper_mimic"]["joints"]) == 5
    assert cfg["asset"]["solver_position_iterations"] >= 64
    assert "finger_joint" in cfg["joint_limits"] and "robotiq_85_left_knuckle_joint" not in cfg["joint_limits"]
    assert "robotiq_85" not in (proj / "build/robot.urdf").read_text()  # URDF のグリッパは外した


def test_rotation_between_maps_gripper_frames():
    from urdf2vla.skills.robot import _rotation_between
    from urdf2vla.urdf.kinematics import quat_rotate

    q = _rotation_between((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0))
    assert all(abs(a - b) < 1e-9 for a, b in zip(quat_rotate(q, (0.0, 0.0, 1.0)), (1.0, 0.0, 0.0)))
    assert all(abs(a - b) < 1e-9 for a, b in zip(quat_rotate(q, (0.0, 1.0, 0.0)), (0.0, 0.0, -1.0)))


def test_unknown_simready_gripper_is_rejected(tmp_path, capsys):
    franka = Path(__file__).parents[1] / "examples/franka/franka_description/robots/panda_arm_hand.urdf"
    proj = make_project(tmp_path, capsys, franka, {"gripper": {"simready": "no_such_gripper"}})
    run(capsys, "inspect_robot", "--project", str(proj))
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "error" and "no_such_gripper" in r["errors"][0] and "robotiq_2f_85" in r["errors"][0]


@pytest.mark.skipif(not HAS_CRX, reason="set URDF2VLA_CRX_URDF to a CRX-3iA URDF to run")
def test_view_lists_pending_questions_and_drops_replaced_gripper(tmp_path, capsys):
    """urdf2vla view: 未回答の質問を出し、回答すると消える。SimReady に付け替えるグリッパは表示用の URDF から外す。"""
    from urdf2vla.project import Project
    from urdf2vla.view import pending_questions, view_urdf

    proj = make_project(tmp_path, capsys, CRX)
    run(capsys, "inspect_robot", "--project", str(proj))
    keys = {q["key"] for q in pending_questions(Project(proj))}
    assert {"arms.0.ee_link", "grippers.0.open_position"} <= keys
    Project(proj).set_answers({"arms.0.ee_link": "robotiq_85_base_link"})
    assert "arms.0.ee_link" not in {q["key"] for q in pending_questions(Project(proj))}
    path, _, _, dropped = view_urdf(Project(proj))
    assert "robotiq_85_left_knuckle_link" in path.read_text() and not dropped

    proj2 = make_project(tmp_path / "sr", capsys, CRX, {"gripper": {"simready": "robotiq_2f_85"}})
    path2, _, _, dropped2 = view_urdf(Project(proj2))
    assert "robotiq_85_left_knuckle_link" in dropped2 and "robotiq_85" not in path2.read_text()
    assert "/tmpproj/" not in path2.read_text() and "meshes" in path2.read_text()  # メッシュはホストのパスのまま（コンテナのパスではない）


def _arm_only_urdf(path: Path, camera_mesh: str | None, arm_mesh: str | None = None) -> Path:
    """6 軸の腕だけ（グリッパなし）+ 手首のカメラ。メッシュは別のマシンの絶対パス（見つからない）にできる。"""

    def mesh_link(name, mesh):
        geo = f'<visual><geometry><mesh filename="{mesh}"/></geometry></visual>' if mesh else ""
        return _link(name).replace("</link>", geo + "</link>")

    parts = [_link("base_link", 10.0)]
    prev = "base_link"
    for i in range(1, 7):
        parts += [mesh_link(f"link{i}", arm_mesh if i == 3 else None), _joint(f"joint{i}", "revolute", prev, f"link{i}", lo=-3.0, hi=3.0)]
        prev = f"link{i}"
    parts += [_link("tool0"), _joint("tool0_joint", "fixed", "link6", "tool0", xyz="0 0 0.08")]
    parts += [mesh_link("camera_link", camera_mesh), _joint("camera_joint", "fixed", "link6", "camera_link", xyz="0.05 0 0.03")]
    path.write_text(f'<robot name="arm6">{"".join(parts)}</robot>')
    return path


def test_missing_accessory_mesh_is_a_warning_but_a_missing_arm_mesh_is_an_error(tmp_path, capsys):
    gone = "file:///root/other_ws/install/cam_description/share/cam_description/meshes/cam.stl"
    proj = make_project(tmp_path / "a", capsys, _arm_only_urdf(tmp_path / "a.urdf", gone), {"gripper": {"simready": "robotiq_2f_85"}})
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "ok" and any("camera_link" in w and "形なし" in w for w in r["warnings"])
    assert run(capsys, "configure_robot", "--project", str(proj))["status"] == "ok"

    proj = make_project(tmp_path / "b", capsys, _arm_only_urdf(tmp_path / "b.urdf", None, arm_mesh=gone), {"gripper": {"simready": "robotiq_2f_85"}})
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert r["status"] == "error" and "メッシュ" in r["errors"][0]


def test_arm_without_gripper_gets_the_simready_gripper_on_its_tool_frame(tmp_path, capsys):
    """腕だけの URDF に gripper: {simready: ...} の 1 行: 取付先（tool0）と向き（最後の関節の軸の外向き）を自動で決める。"""
    proj = make_project(tmp_path, capsys, _arm_only_urdf(tmp_path / "arm.urdf", None), {"gripper": {"simready": "robotiq_2f_85"}})
    run(capsys, "inspect_robot", "--project", str(proj))
    r = run(capsys, "configure_robot", "--project", str(proj))
    assert r["status"] == "ok" and any("'tool0'" in w for w in r["warnings"])
    cfg = yaml.safe_load((proj / "generated/robot_config.yaml").read_text())
    assert cfg["grippers"][0]["mount_link"] == "tool0"
    assert cfg["arms"][0]["approach_axis"][2] > 0.99  # joint6 の軸（+z）の、tool0 がある側


def test_no_gripper_question_mentions_attaching_one(tmp_path, capsys):
    proj = make_project(tmp_path, capsys, _arm_only_urdf(tmp_path / "arm.urdf", None))
    r = run(capsys, "inspect_robot", "--project", str(proj))
    q = next(q for q in r["questions"] if q["key"] == "robot.gripper_joints")
    assert "simready" in q["prompt"] and "tool0" in q["prompt"]
