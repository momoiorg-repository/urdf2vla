"""configure_vla_interface（ホスト）と Phase 2 の sim Skill の宣言。"""

from __future__ import annotations

from urdf2vla.project import Project
from urdf2vla.registry import Arg, declare_sim_skill, skill
from urdf2vla.result import SkillResult
from urdf2vla.schema.camera import CamerasConfig
from urdf2vla.schema.robot import RobotConfig
from urdf2vla.schema.task import TaskConfig
from urdf2vla.schema.vla import ActionSpec, Group, ImageSpec, StateSpec, VlaInterface


def build_vla_interface(robot: RobotConfig, cameras: CamerasConfig, task: TaskConfig, vla_type: str = "gr00t") -> VlaInterface:
    groups: list[Group] = []
    for arm in robot.arms:
        groups.append(Group(name=f"{arm.name}_arm", kind="joint_position", joints=list(arm.joints)))
    for g in robot.grippers:
        groups.append(Group(name=g.name, kind="gripper_scalar", gripper=g.name))
    return VlaInterface(
        vla_type=vla_type,
        control_hz=round(task.control_hz, 6),
        images=[ImageSpec(key=c.name, camera=c.name, width=c.width, height=c.height) for c in cameras.cameras],
        state=StateSpec(groups=groups),
        action=ActionSpec(groups=[g.model_copy() for g in groups]),
        language_instruction=task.language_instruction,
    )


@skill(
    "configure_vla_interface",
    phase=2,
    runs_in="host",
    summary="VLA の observation（画像・関節状態）と action（関節目標・グリッパ）の仕様を決める",
    inputs=["generated/robot_config.yaml", "generated/cameras.yaml", "generated/task.yaml"],
    outputs=["generated/vla_interface.yaml"],
    requires=["launch_sim"],
    args=[Arg(("--vla-type",), {"default": "gr00t", "choices": ["gr00t"], "help": "対象の VLA"})],
)
def configure_vla_interface(project: Project, opts: dict, result: SkillResult, log) -> None:
    robot = RobotConfig.model_validate(project.read_generated("robot_config.yaml"))
    cameras = CamerasConfig.model_validate(project.read_generated("cameras.yaml"))
    task = TaskConfig.model_validate(project.read_generated("task.yaml"))
    vla = build_vla_interface(robot, cameras, task, opts.get("vla_type") or "gr00t")
    path = project.write_generated("vla_interface.yaml", vla.model_dump(mode="json"), section="vla_interface")
    VlaInterface.model_validate(project.read_generated("vla_interface.yaml"))
    result.outputs["vla_interface"] = project.rel(path)
    result.data = {
        "state_dim": vla.state.dimension,
        "action_dim": vla.action.dimension,
        "state_names": vla.state.names,
        "images": [i.key for i in vla.images],
        "control_hz": vla.control_hz,
    }
    result.next = ["setup_teleoperation"]


declare_sim_skill(
    "setup_teleoperation",
    phase=2,
    summary="teleop 用設定（IK で手先を動かす）を作り、指令した方向に手先が動くかを Isaac Sim で検査する",
    inputs=["generated/robot_config.yaml", "generated/vla_interface.yaml"],
    outputs=["generated/teleop.yaml", "build/teleop_report.yaml"],
    requires=["configure_vla_interface"],
    args=[Arg(("--timeout",), {"type": float, "default": 900.0, "help": "秒"})],
)

declare_sim_skill(
    "collect_dataset",
    phase=2,
    summary="teleop（scripted / keyboard / socket）で Pick & Place のデモを記録し HDF5 に保存する（成功したデモのみ）",
    inputs=["generated/teleop.yaml", "generated/vla_interface.yaml"],
    outputs=["datasets/<name>.hdf5", "build/collect_report.yaml"],
    requires=["setup_teleoperation"],
    args=[
        Arg(("--device",), {"default": "scripted", "choices": ["scripted", "keyboard", "socket", "mimic"], "help": "操作デバイス（mimic: Isaac Lab Mimic で生成したエピソードを再生して記録する）"}),
        Arg(("--source",), {"default": None, "help": "--device mimic: Mimic で生成した HDF5（プロジェクトからの相対パス）"}),
        Arg(("--num-demos",), {"type": int, "default": 10, "help": "集める成功デモ数"}),
        Arg(("--max-attempts",), {"type": int, "default": 0, "help": "試行回数の上限（0 = num-demos の 3 倍）"}),
        Arg(("--dataset",), {"default": "demos", "help": "datasets/<名前>.hdf5"}),
        Arg(("--seed",), {"type": int, "default": 0}),
        Arg(("--livestream",), {"action": "store_true", "help": "WebRTC で GUI を配信（keyboard / socket 操作用）"}),
        Arg(("--timeout",), {"type": float, "default": 3600.0, "help": "秒（keyboard / socket では無制限）"}),
    ],
)
