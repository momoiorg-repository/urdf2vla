import ast

from urdf2vla.gr00t.modality import modality_config_py, modality_json
from urdf2vla.schema.vla import ActionSpec, Group, ImageSpec, StateSpec, VlaInterface


def _vla(two_arms: bool = False) -> VlaInterface:
    groups = [Group(name="left_arm", kind="joint_position", joints=[f"l{i}" for i in range(7)])]
    if two_arms:
        groups.append(Group(name="right_arm", kind="joint_position", joints=[f"r{i}" for i in range(7)]))
    groups.append(Group(name="left_gripper", kind="gripper_scalar", gripper="left_gripper"))
    return VlaInterface(
        control_hz=20.0,
        images=[ImageSpec(key="wrist_cam", camera="wrist_cam", width=256, height=256)],
        state=StateSpec(groups=groups),
        action=ActionSpec(groups=[g.model_copy() for g in groups]),
        language_instruction="pick",
    )


def test_modality_json_ranges_are_contiguous():
    m = modality_json(_vla(two_arms=True))
    assert m["state"]["left_arm"] == {"start": 0, "end": 7, "original_key": "observation.state"}
    assert m["state"]["right_arm"]["start"] == 7 and m["state"]["right_arm"]["end"] == 14
    assert m["action"]["left_gripper"] == {"start": 14, "end": 15}
    assert m["video"]["wrist_cam"]["original_key"] == "observation.images.wrist_cam"


def test_modality_config_is_valid_python_and_arm_is_relative():
    src = modality_config_py(_vla())
    ast.parse(src)
    assert "state_key='left_arm'" in src  # arm は state からの相対行動
    assert src.count("ActionRepresentation.ABSOLUTE") == 1  # グリッパは絶対値
    assert "list(range(16))" in src
