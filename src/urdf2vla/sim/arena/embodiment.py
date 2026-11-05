"""urdf2vla のロボット（URDF から作った RobotConfig と USD）を Arena の Embodiment にする。

Arena の Embodiment は登録済みのロボット（Franka・DROID など）を前提にしているが、ここでは
URDF の解析結果から実行時に組み立てる（Arena に URDF の読み込みはない）。
- policy: 関節目標（絶対値）+ グリッパ開閉。VLA の推論・評価・ラベル再生と同じ行動
- teleop: 目標手先（TCP）姿勢を IK で関節目標に変換 + グリッパ開閉。記録するラベルは IK が出した関節目標
"""

from __future__ import annotations

import re

from urdf2vla.schema.camera import CameraConfig

FLOOR_Z = -0.75  # 床の高さ（天板上面 = 0 = ロボット base の高さ）


def _expr(names: list[str]) -> list[str]:
    """Isaac Lab の関節指定は正規表現なので、名前をそのまま一致させる。"""
    return [re.escape(n) for n in names]


def camera_cfg(cam: CameraConfig, link_paths: dict[str, str]):
    import isaaclab.sim as sim_utils
    from isaaclab.sensors import CameraCfg

    if cam.mount == "link":
        rel = link_paths.get(cam.mount_body)
        if rel is None:
            raise ValueError(f"カメラ {cam.name} の取付先 {cam.mount_body} が USD にありません")
        prim_path = f"{{ENV_REGEX_NS}}/Robot/{rel}/{cam.name}"
    else:
        prim_path = f"{{ENV_REGEX_NS}}/{cam.name}"
    if cam.intrinsic_matrix:
        spawn = sim_utils.PinholeCameraCfg.from_intrinsic_matrix(
            intrinsic_matrix=cam.intrinsic_matrix, width=cam.width, height=cam.height, clipping_range=tuple(cam.clipping_range)
        )
    else:
        spawn = sim_utils.PinholeCameraCfg(
            focal_length=cam.focal_length,
            horizontal_aperture=cam.horizontal_aperture,
            clipping_range=tuple(cam.clipping_range),
        )
    return CameraCfg(
        prim_path=prim_path,
        update_period=0.0,
        height=cam.height,
        width=cam.width,
        data_types=list(cam.data_types),
        spawn=spawn,
        offset=CameraCfg.OffsetCfg(pos=tuple(cam.pos), rot=tuple(cam.rot_xyzw), convention=cam.convention),
        # link に付けたカメラは動くので、data.pos_w / quat_w を毎回更新する（既定では初期化時の値のまま）
        update_latest_camera_pose=cam.mount == "link",
    )


def make_embodiment(c, mode: str):
    """Configs（urdf2vla.sim.common）→ Arena の Embodiment（インスタンス）。mode は "policy" か "teleop"。"""
    import isaaclab.envs.mdp as mdp
    import isaaclab.sim as sim_utils
    from isaaclab.assets import ArticulationCfg, AssetBaseCfg
    from isaaclab.controllers import DifferentialIKControllerCfg
    from isaaclab.managers import ActionTermCfg, ObservationGroupCfg, ObservationTermCfg, SceneEntityCfg
    from isaaclab.sensors import CameraCfg
    from isaaclab_arena.embodiments.common.arm_mode import ArmMode
    from isaaclab_arena.embodiments.embodiment_base import EmbodimentBase
    from isaaclab_arena.utils.cameras import ArenaCameraCfg
    from isaaclab_arena.utils.configclass import make_configclass

    from urdf2vla.sim.robot_builder import articulation_cfg

    robot = c.robot
    if len(robot.arms) != 1:
        raise ValueError(
            f"双腕（arm {len(robot.arms)} 本）のロボットは、Isaac Lab 3.0 版の urdf2vla ではまだ環境を作れません"
            "（import_robot の USD 変換と検査までは使えます）。単腕のロボットで試してください。"
        )
    link_paths = c.imported["usd"]["link_paths"]

    # ---- scene: ロボットと台座（台座はロボットが宙に浮いて見えないようにする見た目だけ。当たり判定なし）
    base_z = robot.base_pose.pos[2]
    stand_h = base_z - FLOOR_Z
    stand = AssetBaseCfg(
        prim_path="{ENV_REGEX_NS}/Stand",
        spawn=sim_utils.CuboidCfg(
            size=(0.22, 0.22, stand_h - 0.002), collision_props=None, visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.42, 0.42, 0.45))
        ),
        init_state=AssetBaseCfg.InitialStateCfg(pos=(robot.base_pose.pos[0], robot.base_pose.pos[1], FLOOR_Z + stand_h / 2 - 0.002)),
    )
    SceneCfg = make_configclass(
        "Urdf2VlaRobotSceneCfg",
        [
            ("robot", ArticulationCfg, articulation_cfg(robot, c.imported["usd_path"], "{ENV_REGEX_NS}/Robot")),
            ("stand", AssetBaseCfg, stand),
        ],
    )

    # ---- actions（ベクトルの並び = arm ごとの関節目標 / TCP 姿勢 ..., グリッパごとの ±1）
    actions = []
    for arm in robot.arms:
        if mode == "teleop":
            term = mdp.DifferentialInverseKinematicsActionCfg(
                asset_name="robot",
                joint_names=_expr(arm.joints),
                body_name=re.escape(arm.ik_body),
                # 目標手先姿勢（base 座標の絶対値）。teleop デバイスの相対指令は TargetIntegrator が積算する
                controller=DifferentialIKControllerCfg(command_type="pose", use_relative_mode=False, ik_method=arm.ik_method),
                scale=1.0,
                body_offset=mdp.DifferentialInverseKinematicsActionCfg.OffsetCfg(
                    pos=tuple(arm.tcp_offset.pos), rot=tuple(arm.tcp_offset.rot_xyzw)
                ),
            )
        else:
            term = mdp.JointPositionActionCfg(
                asset_name="robot", joint_names=_expr(arm.joints), scale=1.0, use_default_offset=False, preserve_order=True
            )
        actions.append((f"{arm.name}_arm", ActionTermCfg, term))
    for g in robot.grippers:
        # 入力 >= 0 で開く / < 0 で閉じる。mimic の follower も含めて全関節に指令する（開閉値は gripper spec の 1 か所から）
        actions.append(
            (
                g.name,
                ActionTermCfg,
                mdp.BinaryJointPositionActionCfg(
                    asset_name="robot",
                    joint_names=_expr(g.joints),
                    open_command_expr={re.escape(j): g.open[j] for j in g.joints},
                    close_command_expr={re.escape(j): g.close[j] for j in g.joints},
                ),
            )
        )
    ActionsCfg = make_configclass("Urdf2VlaActionsCfg", actions)

    # ---- observations（関節角。カメラ画像は Arena が camera_obs グループとして足す）
    def _post_init(self):
        self.enable_corruption = False
        self.concatenate_terms = False

    joint_pos = ObservationTermCfg(
        func=mdp.joint_pos,
        params={"asset_cfg": SceneEntityCfg("robot", joint_names=_expr(robot.controlled_joints), preserve_order=True)},
    )
    PolicyCfg = make_configclass(
        "Urdf2VlaPolicyObsCfg", [("joint_pos", ObservationTermCfg, joint_pos)], bases=(ObservationGroupCfg,), namespace={"__post_init__": _post_init}
    )
    ObservationsCfg = make_configclass("Urdf2VlaObservationsCfg", [("policy", PolicyCfg, PolicyCfg())])

    # ---- cameras（Arena のカメラリグ。フィールド名 = シーン上のカメラ名）
    CameraRigCfg = make_configclass(
        "Urdf2VlaCameraCfg",
        [(cam.name, CameraCfg, camera_cfg(cam, link_paths)) for cam in c.cameras.cameras],
        bases=(ArenaCameraCfg,),
    )

    class Urdf2VlaEmbodiment(EmbodimentBase):
        registry_name = "urdf2vla_robot"
        default_arm_mode = ArmMode.SINGLE_ARM

        def __init__(self):
            super().__init__(enable_cameras=True)
            self.scene_config = SceneCfg()
            self.action_config = ActionsCfg()
            self.observation_config = ObservationsCfg()
            self.camera_config = CameraRigCfg()
            self.event_config = None  # reset は Task の events にまとめる（順序を保証するため）
            self.reward_config = None
            self.mimic_env = None

        def get_command_body_name(self) -> str:
            return robot.arms[0].ik_body

    return Urdf2VlaEmbodiment()
