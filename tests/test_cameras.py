"""URDF のカメラの検出（urdf2vla.urdf.cameras）・configure_camera の既定・`urdf2vla view` のカメラ編集の計算。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from urdf2vla import camera_edit as CE
from urdf2vla.cli import main
from urdf2vla.urdf.cameras import default_camera_names, find_cameras
from urdf2vla.urdf.kinematics import Transform, quat_from_rpy, quat_rotate
from urdf2vla.urdf.parser import parse_urdf

ROOT = Path(__file__).resolve().parents[1]
FRANKA = ROOT / "examples/franka/franka_description/robots/panda_arm_hand.urdf"
OPENARM = ROOT / "external/openarm_description/assets/robot/openarm_v2.0/urdf/example/v2.urdf"


def run(capsys, *argv) -> dict:
    main(list(argv))
    out = capsys.readouterr().out
    return json.loads(out[out.index("{") :])


def _link(name: str, mass: float | None = 1.0) -> str:
    if mass is None:
        return f'<link name="{name}"/>'
    return f'<link name="{name}"><inertial><mass value="{mass}"/><inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01"/></inertial></link>'


def _fixed(parent: str, child: str, xyz: str = "0 0 0", rpy: str = "0 0 0") -> str:
    return f'<joint name="{parent}_to_{child}" type="fixed"><parent link="{parent}"/><child link="{child}"/><origin xyz="{xyz}" rpy="{rpy}"/></joint>'


def _rev(name: str, parent: str, child: str, xyz: str = "0 0 0.2", axis: str = "0 1 0") -> str:
    return (f'<joint name="{name}" type="revolute"><parent link="{parent}"/><child link="{child}"/><origin xyz="{xyz}" rpy="0 0 0"/>'
            f'<axis xyz="{axis}"/><limit lower="-3" upper="3" effort="50" velocity="1"/></joint>')  # fmt: skip


def camera_urdf(path: Path) -> Path:
    """腕 1 本（手にカメラの frame）+ 頭に ZED 風のステレオ + 胴に RealSense 風（color / depth、Gazebo の視野角つき）。"""
    parts = [_link("base_link", 10.0)]
    prev = "base_link"
    # 6 軸の腕（UR に似た軸の並び: 旋回・肩・肘・手首 3 軸）。最後の link が手
    layout = [("0 0 0.15", "0 0 1"), ("0 0 0.1", "0 1 0"), ("0 0 0.4", "0 1 0"), ("0 0 0.35", "0 1 0"), ("0 0 0.08", "0 0 1"), ("0 0 0.08", "0 1 0")]
    for i, (xyz, axis) in enumerate(layout, start=1):
        link = "hand" if i == 6 else f"link{i}"
        parts += [_link(link), _rev(f"joint{i}", prev, link, xyz=xyz, axis=axis)]
        prev = link
    for f, y in (("finger_left", 0.02), ("finger_right", -0.02)):  # 直動の 2 本指
        mimic = '<mimic joint="finger_left"/>' if f == "finger_right" else ""
        parts += [_link(f, 0.05), f'<joint name="{f}" type="prismatic"><parent link="hand"/><child link="{f}"/><origin xyz="0 {y} 0.1"/>'
                  f'<axis xyz="0 {1 if y > 0 else -1} 0"/><limit lower="0" upper="0.04" effort="20" velocity="0.1"/>{mimic}</joint>']  # fmt: skip
    parts += [
        _link("hand_camera_frame", None), _fixed("hand", "hand_camera_frame", "0.05 0 -0.05", "0 0.5 0"),
        # 頭: 取付部品（mount）→ ZED（camera_link → center → left/right → optical）
        _link("head", 0.5), _link("head_camera_mount", None), _link("zed_camera_link", 0.1), _link("zed_camera_center", None),
        _link("zed_left_camera_frame", None), _link("zed_left_camera_frame_optical", None),
        _link("zed_right_camera_frame", None), _link("zed_right_camera_frame_optical", None),
        _fixed("base_link", "head", "0 0 0.8"), _fixed("head", "head_camera_mount", "0.05 0 0.05"),
        _fixed("head_camera_mount", "zed_camera_link"), _fixed("zed_camera_link", "zed_camera_center", "0 0 0.01"),
        _fixed("zed_camera_center", "zed_left_camera_frame", "0 0.03 0"), _fixed("zed_camera_center", "zed_right_camera_frame", "0 -0.03 0"),
        _fixed("zed_left_camera_frame", "zed_left_camera_frame_optical", rpy=f"{-math.pi / 2} 0 {-math.pi / 2}"),
        _fixed("zed_right_camera_frame", "zed_right_camera_frame_optical", rpy=f"{-math.pi / 2} 0 {-math.pi / 2}"),
        # 胴: RealSense（color と depth。depth は使わない）
        _link("camera_link", 0.07), _link("camera_color_frame", None), _link("camera_color_optical_frame", None),
        _link("camera_depth_frame", None), _link("camera_depth_optical_frame", None),
        _fixed("base_link", "camera_link", "0.1 0 0.5"), _fixed("camera_link", "camera_color_frame"), _fixed("camera_color_frame", "camera_color_optical_frame"),
        _fixed("camera_link", "camera_depth_frame"), _fixed("camera_depth_frame", "camera_depth_optical_frame"),
        '<gazebo reference="camera_link"><sensor type="camera" name="rs"><camera><horizontal_fov>1.5184</horizontal_fov></camera></sensor></gazebo>',
    ]  # fmt: skip
    path.write_text('<robot name="camrobot">' + "".join(parts) + "</robot>")
    return path


def test_find_cameras_groups_devices_and_classifies(tmp_path):
    model = parse_urdf(camera_urdf(tmp_path / "cam.urdf"))
    cams = find_cameras(model, [("main", [f"joint{i}" for i in range(1, 7)])])
    by = {c.link: c for c in cams}
    assert set(by) == {"hand_camera_frame", "zed_left_camera_frame_optical", "camera_color_optical_frame"}
    hand = by["hand_camera_frame"]
    assert (hand.arm, hand.role, hand.convention) == ("main", "wrist", "world")  # optical でない frame は x 前方
    zed = by["zed_left_camera_frame_optical"]
    assert (zed.arm, zed.role, zed.convention) == (None, "head", "ros")
    assert zed.alternatives == ["zed_right_camera_frame_optical"]  # ステレオは左目を使い、右目は候補に残す
    rs = by["camera_color_optical_frame"]
    assert rs.hfov_deg == pytest.approx(math.degrees(1.5184), abs=0.01)  # Gazebo のセンサは機器の camera_link に付いている
    assert cams[0].link == "hand_camera_frame"  # arm のカメラが先
    assert default_camera_names(cams, 1) == ["wrist_cam", "head_cam", "head_cam_2"]


def test_franka_has_no_urdf_cameras_and_keeps_droid_default(tmp_path, capsys):
    model = parse_urdf(FRANKA)
    assert find_cameras(model, [("main", [f"panda_joint{i}" for i in range(1, 8)])]) == []
    proj = tmp_path / "proj"
    run(capsys, "init", str(proj), "--urdf", str(FRANKA))
    for skill in ("inspect_robot", "configure_robot", "configure_task", "configure_camera"):
        r = run(capsys, skill, "--project", str(proj))
        assert r["status"] == "ok", (skill, r)
    assert r["data"]["source"] == "droid"
    cams = yaml.safe_load((proj / "generated/cameras.yaml").read_text())["cameras"]
    assert [c["name"] for c in cams] == ["wrist_cam", "left_cam", "right_cam"]


def test_configure_camera_uses_urdf_cameras_and_view_answers(tmp_path, capsys):
    urdf = camera_urdf(tmp_path / "cam.urdf")
    proj = tmp_path / "proj"
    run(capsys, "init", str(proj), "--urdf", str(urdf))
    cfg = yaml.safe_load((proj / "project.yaml").read_text())
    cfg["robot"]["end_effector"] = "hand"
    (proj / "project.yaml").write_text(yaml.safe_dump(cfg))
    r = run(capsys, "inspect_robot", "--project", str(proj))
    assert [c["link"] for c in r["data"]["cameras"]][0] == "hand_camera_frame"
    for skill in ("configure_robot", "configure_task", "configure_camera"):
        r = run(capsys, skill, "--project", str(proj))
        if r["status"] == "needs_input":  # このロボットの End Effector などの質問には候補の先頭で答える
            run(capsys, "answer", "--project", str(proj), *[f"{q['key']}={q['candidates'][0]}" for q in r["questions"]])
            r = run(capsys, skill, "--project", str(proj))
        assert r["status"] == "ok", (skill, r)
    assert r["data"]["source"] == "urdf"
    cams = {c["name"]: c for c in yaml.safe_load((proj / "generated/cameras.yaml").read_text())["cameras"]}
    assert list(cams) == ["wrist_cam", "head_cam", "head_cam_2"]  # 手のカメラが URDF にあるので wrist の preset は使わず、外部カメラも足さない
    by_link = {c["parent_link"]: c for c in cams.values()}
    hand = cams["wrist_cam"]
    assert hand["preset"] == "urdf" and hand["mount_body"] == "hand" and hand["convention"] == "world"
    assert hand["clipping_range"][0] == pytest.approx(0.03)
    assert CE.hfov_deg(hand) == pytest.approx(80.0, abs=0.05)  # URDF に視野角がなければ既定（手先 80°・頭 70°）
    assert CE.hfov_deg(by_link["camera_color_optical_frame"]) == pytest.approx(math.degrees(1.5184), abs=0.05)  # Gazebo の値
    assert CE.hfov_deg(by_link["zed_left_camera_frame_optical"]) == pytest.approx(70.0, abs=0.05)
    # 光学 frame の前方（z）は、手のカメラの frame の x 軸（hand から見て rpy (0, 0.5, 0) で回した x）
    fwd = quat_rotate(CE.optical_local(hand).rot, (0.0, 0.0, 1.0))
    expect = quat_rotate(quat_from_rpy(0.0, 0.5, 0.0), (1.0, 0.0, 0.0))
    assert max(abs(a - b) for a, b in zip(fwd, expect)) < 1e-6

    # `urdf2vla view` で保存したカメラ（answers.yaml の cameras）が project.yaml・既定より優先する
    from urdf2vla.project import Project

    p = Project(proj)
    moved = Transform((0.6, 0.0, 0.9), (0.0, 0.0, 0.0, 1.0))
    p.set_answers({"cameras": [CE.original_spec(hand, {}), CE.explicit_spec("top_cam", None, moved, 60.0, 320, 240)]})
    r = run(capsys, "configure_camera", "--project", str(proj))
    assert r["status"] == "ok" and r["data"]["source"] == "view"
    cams = {c["name"]: c for c in yaml.safe_load((proj / "generated/cameras.yaml").read_text())["cameras"]}
    assert list(cams) == ["wrist_cam", "top_cam"]
    assert cams["top_cam"]["mount"] == "world" and cams["top_cam"]["pos"] == [0.6, 0.0, 0.9] and cams["top_cam"]["width"] == 320
    assert CE.hfov_deg(cams["top_cam"]) == pytest.approx(60.0, abs=0.01)
    p.clear_answers(["cameras"])
    assert "cameras" not in p.answers()
    r = run(capsys, "configure_camera", "--project", str(proj))
    assert r["data"]["source"] == "urdf"


def test_default_specs_fill_missing_roles():
    from types import SimpleNamespace

    from urdf2vla.skills.scene import default_camera_specs
    from urdf2vla.urdf.cameras import UrdfCamera

    cfg = SimpleNamespace(arms=[SimpleNamespace(name="left"), SimpleNamespace(name="right")])
    head_only = [("head_cam", UrdfCamera(link="head_optical", convention="ros"))]
    specs = default_camera_specs(cfg, head_only)
    assert [s.get("name") for s in specs] == ["left_wrist_cam", "right_wrist_cam", "head_cam"]  # 手のカメラは preset で補う
    assert [s["preset"] for s in specs] == ["wrist", "wrist", "urdf"]
    left_hand_only = [("left_wrist_cam", UrdfCamera(link="l_cam", convention="world", arm="left", role="wrist"))]
    specs = default_camera_specs(cfg, left_hand_only)
    assert [(s["preset"], s.get("name")) for s in specs] == [("urdf", "left_wrist_cam"), ("wrist", "right_wrist_cam"), ("left", None), ("right", None)]
    assert default_camera_specs(cfg, []) == [{"preset": "wrist"}, {"preset": "left"}, {"preset": "right"}]


def test_camera_edit_roundtrip_through_explicit_spec(tmp_path):
    """画面で動かしたカメラ（world の姿勢）→ 取り付け先から見た姿勢 → 明示指定 → configure_camera の解釈で同じ world の姿勢に戻る。"""
    from urdf2vla.result import SkillResult
    from urdf2vla.skills.scene import _explicit_camera
    from urdf2vla.urdf import analysis as A

    urdf = camera_urdf(tmp_path / "cam.urdf")
    model = parse_urdf(urdf)
    q = {"joint1": 0.3, "joint2": -0.4}
    frame = "hand_camera_frame"  # 質量なしの frame に付ける（剛体の祖先 hand に付け替えられる）
    frame_world = A.relative_transform(model, "base_link", frame, q)
    cam_world = Transform((0.3, 0.1, 0.5), quat_from_rpy(0.2, 0.9, -0.4))
    spec = CE.explicit_spec("c", frame, CE.relative_to(frame_world, cam_world), 75.0, 256, 256)
    cfg = type("Cfg", (), {"asset": type("A", (), {"host_path": str(urdf)})()})()
    cam = _explicit_camera(cfg, spec, SkillResult(skill="t")).model_dump(mode="json")
    assert cam["mount_body"] == "hand"
    back = A.relative_transform(model, "base_link", "hand", q) @ CE.optical_local(cam)
    assert max(abs(a - b) for a, b in zip(back.pos, cam_world.pos)) < 1e-4
    dot = abs(sum(a * b for a, b in zip(back.rot, cam_world.rot)))
    assert dot > 1 - 1e-6
    assert CE.hfov_deg(cam) == pytest.approx(75.0, abs=0.01)


def test_conventions_to_optical():
    # world（x 前方・z 上）の frame の x が光学の z（前方）、-z が光学の y（画像の下）
    t = CE.optical_local({"pos": [0, 0, 0], "rot_xyzw": [0, 0, 0, 1], "convention": "world"})
    assert [round(v, 6) for v in quat_rotate(t.rot, (0, 0, 1))] == [1.0, 0.0, 0.0]
    assert [round(v, 6) for v in quat_rotate(t.rot, (0, 1, 0))] == [0.0, 0.0, -1.0]
    t = CE.optical_local({"pos": [0, 0, 0], "rot_xyzw": [0, 0, 0, 1], "convention": "opengl"})
    assert [round(v, 6) for v in quat_rotate(t.rot, (0, 0, 1))] == [0.0, 0.0, -1.0]  # opengl は -z 前方


@pytest.mark.skipif(not OPENARM.exists(), reason="git clone https://github.com/enactic/openarm_description external/openarm_description")
def test_openarm_urdf_cameras():
    model = parse_urdf(OPENARM)
    arms = [("left", [f"openarm_left_joint{i}" for i in range(1, 8)]), ("right", [f"openarm_right_joint{i}" for i in range(1, 8)])]
    cams = find_cameras(model, arms)
    assert [(c.link, c.arm, c.role, c.convention) for c in cams] == [
        ("openarm_left_camera_view_frame", "left", "wrist", "world"),
        ("openarm_right_camera_view_frame", "right", "wrist", "world"),
        ("openarm_body_zed_left_camera_frame_optical", None, "head", "ros"),
    ]
    assert default_camera_names(cams, 2) == ["left_wrist_cam", "right_wrist_cam", "head_cam"]
