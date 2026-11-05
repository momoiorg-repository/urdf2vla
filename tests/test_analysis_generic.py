"""DUAL_SCORPION で見つかった一般的なケース（正面方向・直列グリッパ・片側の顎の把持点）のテスト。"""

from __future__ import annotations

from pathlib import Path

from urdf2vla.schema.robot import GripperCandidate
from urdf2vla.urdf import analysis as A
from urdf2vla.urdf.parser import parse_urdf

INERTIA = '<inertial><mass value="0.5"/><inertia ixx="0.001" ixy="0" ixz="0" iyy="0.001" iyz="0" izz="0.001"/></inertial>'


def _link(name, boxes=()):
    cols = "".join(
        f'<collision><origin xyz="{x} {y} {z}" rpy="0 0 0"/><geometry><box size="{sx} {sy} {sz}"/></geometry></collision>'
        for (x, y, z), (sx, sy, sz) in boxes
    )
    return f'<link name="{name}">{INERTIA}{cols}</link>'


def _joint(name, jtype, parent, child, xyz, axis="1 0 0", limit=True):
    lim = '<limit lower="-1.5" upper="1.5" effort="10" velocity="1"/>' if limit and jtype != "fixed" else ""
    return f'<joint name="{name}" type="{jtype}"><parent link="{parent}"/><child link="{child}"/><origin xyz="{xyz}" rpy="0 0 0"/><axis xyz="{axis}"/>{lim}</joint>'


def side_by_side_bimanual(path: Path) -> Path:
    """肩が x 方向に ±0.2m 並び、腕が +y に伸びる双腕（DUAL_SCORPION と同じ配置）。指は arm の最後の関節（直列）。"""
    parts = [_link("base_link")]
    for side, x in (("R", 0.2), ("L", -0.2)):
        prev = "base_link"
        for i, xyz in enumerate([f"{x} 0 0.37", "0 0.06 0", "0 0.06 0", "0 0.06 0"]):
            link = f"{side.lower()}_link_{i + 1}"
            parts += [_link(link), _joint(f"joint{i}_{side}", "revolute", prev, link, xyz)]
            prev = link
        parts += [_link(f"{side.lower()}_jaw"), _joint(f"joint9_{side}", "revolute", prev, f"{side.lower()}_jaw", "0 0.02 0", "0 0 1")]
    path.write_text(f'<robot name="side_by_side">{"".join(parts)}</robot>')
    return path


def test_front_axis_side_by_side_is_plus_y(tmp_path):
    m = parse_urdf(side_by_side_bimanual(tmp_path / "r.urdf"))
    g = A.find_grippers(m, "base_link", forced=["joint9_R", "joint9_L"])
    arms, _ = A.find_arms(m, "base_link", g)
    front, why = A.front_axis_guess(m, "base_link", arms)
    assert front == "+y", why
    assert {a.name for a in arms} == {"right", "left"}  # _R / _L から左右を判定


def test_forced_gripper_joints_are_grouped_per_mount(tmp_path):
    m = parse_urdf(side_by_side_bimanual(tmp_path / "r.urdf"))
    g = A.find_grippers(m, "base_link", forced=["joint9_R", "joint9_L"])
    assert sorted(x.mount_link for x in g) == ["l_link_4", "r_link_4"]
    arms, _ = A.find_arms(m, "base_link", g)
    assert all(len(a.joints) == 4 and a.ee_candidates[0].link.endswith("_link_4") for a in arms)


def test_front_axis_coincident_shoulders_defaults_to_plus_x(tmp_path):
    parts = [_link("base_link")]
    for side in ("left", "right"):
        prev = "base_link"
        for i in range(3):
            link = f"{side}_l{i}"
            parts += [_link(link), _joint(f"{side}_j{i}", "revolute", prev, link, "0 0 0.1")]
            prev = link
    (tmp_path / "c.urdf").write_text(f'<robot name="c">{"".join(parts)}</robot>')
    m = parse_urdf(tmp_path / "c.urdf")
    arms, _ = A.find_arms(m, "base_link", [])
    assert A.front_axis_guess(m, "base_link", arms)[0] == "+x"


def _hand_urdf(path: Path, single_jaw: bool) -> Path:
    """接近方向 +y のハンド。single_jaw なら固定側の顎（hand の一部）と、z 軸まわりに回る顎 1 枚。
    そうでなければ左右対称に x 方向へ動く直動の指 2 本。"""
    jaw = ((0.0, 0.04, 0.0), (0.01, 0.08, 0.01))
    if single_jaw:
        parts = [
            _link("hand", boxes=[((-0.015, 0.06, 0.0), (0.01, 0.08, 0.01))]),
            _link("jaw", boxes=[jaw]),
            _joint("jaw_joint", "revolute", "hand", "jaw", "0.015 0.02 0", "0 0 1"),
        ]
    else:
        parts = [
            _link("hand"),
            _link("f1", boxes=[jaw]),
            _link("f2", boxes=[jaw]),
            _joint("f1_joint", "prismatic", "hand", "f1", "0.015 0.02 0", "1 0 0"),
            _joint("f2_joint", "prismatic", "hand", "f2", "-0.015 0.02 0", "-1 0 0"),
        ]
    path.write_text(f'<robot name="h">{"".join(parts)}</robot>')
    return path


def test_tcp_single_jaw_shifts_toward_open_side(tmp_path):
    m = parse_urdf(_hand_urdf(tmp_path / "s.urdf", single_jaw=True))
    g = GripperCandidate(mount_link="hand", joints=["jaw_joint"], leader="jaw_joint", joint_type="revolute")
    closed, _ = A.tcp_point(m, "hand", g, [0.0, 1.0, 0.0], {}, {"jaw_joint": 0.0})
    opened, src = A.tcp_point(m, "hand", g, [0.0, 1.0, 0.0], {}, {"jaw_joint": -0.8})
    assert src == "collision_mesh"
    assert opened[0] > closed[0] + 0.01  # 開いた顎の側（+x）へずれる
    assert abs(opened[1] - (0.10 - A.PAD_FROM_TIP)) < 1e-3  # 固定側の顎の先端（y=0.10）から PAD_FROM_TIP 手前


def test_tcp_symmetric_fingers_stays_centered(tmp_path):
    m = parse_urdf(_hand_urdf(tmp_path / "p.urdf", single_jaw=False))
    g = GripperCandidate(mount_link="hand", joints=["f1_joint", "f2_joint"], leader="f1_joint", joint_type="prismatic")
    tcp, _ = A.tcp_point(m, "hand", g, [0.0, 1.0, 0.0], {}, {"f1_joint": 0.03, "f2_joint": 0.03})
    assert abs(tcp[0]) < 1e-6 and abs(tcp[2]) < 1e-6
    assert abs(tcp[1] - (0.02 + 0.04 + 0.04 - A.PAD_FROM_TIP)) < 1e-3
