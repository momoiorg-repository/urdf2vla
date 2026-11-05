"""Robot metadata（inspect_robot の出力）と Robot configuration（configure_robot の出力）。

ロボット固有の事実はすべてここに集約し、Isaac Sim 環境・teleop・データ変換・評価は
この 2 つから派生させる（ロボット固有コードを書かない）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from urdf2vla.schema.quat import IDENTITY_XYZW, accept_legacy_wxyz

Provenance = Literal["urdf", "user", "inferred", "default", "simready"]


class Pose(BaseModel):
    pos: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    rot_xyzw: list[float] = Field(default_factory=lambda: list(IDENTITY_XYZW))  # (x, y, z, w)

    _legacy = model_validator(mode="before")(accept_legacy_wxyz)


# --------------------------------------------------------------------------- metadata


class JointInfo(BaseModel):
    name: str
    type: str
    parent: str
    child: str
    lower: float | None = None
    upper: float | None = None
    effort: float | None = None
    velocity: float | None = None
    mimic_of: str | None = None
    mimic_multiplier: float = 1.0
    mimic_offset: float = 0.0


class LinkInfo(BaseModel):
    name: str
    mass: float | None = None
    rigid: bool  # inertial を持ち、Isaac Sim で剛体になる link
    n_meshes: int = 0


class EECandidate(BaseModel):
    link: str
    reasons: list[str] = Field(default_factory=list)
    rigid: bool = True


class GripperCandidate(BaseModel):
    mount_link: str  # 指関節が付いている link
    joints: list[str]
    leader: str
    joint_type: str
    mimic: dict[str, list[float]] = Field(default_factory=dict)  # follower -> [multiplier, offset]


class ArmCandidate(BaseModel):
    name: str
    joints: list[str]  # root から先端への順
    tip_link: str  # 最後の arm 関節の child
    gripper: int | None = None  # grippers のインデックス
    ee_candidates: list[EECandidate] = Field(default_factory=list)


class MeshReport(BaseModel):
    total: int = 0
    resolved: dict[str, str] = Field(default_factory=dict)  # URDF 内の記述 -> ホスト上の実パス
    unresolved: list[str] = Field(default_factory=list)


class RobotMetadata(BaseModel):
    robot_name: str
    urdf_path: str
    root_link: str
    articulation_root: str
    dummy_root: bool = False  # "world" のような空 link が root の場合 True
    links: list[LinkInfo]
    joints: list[JointInfo]
    movable_joints: list[str]
    arms: list[ArmCandidate]
    grippers: list[GripperCandidate]
    other_joints: list[str] = Field(default_factory=list)  # arm にも gripper にも属さない可動関節
    meshes: MeshReport = Field(default_factory=MeshReport)


# --------------------------------------------------------------------------- configuration


class AssetConfig(BaseModel):
    format: Literal["urdf", "usd"] = "urdf"
    source_path: str  # ユーザが指定した元ファイル（ホスト）
    path: str  # シミュレータが読む正規化済みファイル（ランタイム側パス）
    host_path: str  # 同じファイルのホスト側パス
    fix_base: bool = True
    disable_gravity: bool = True
    self_collision: bool = False
    # 衝突形状。convex_decomposition は指のような凹んだ形を忠実にするが、変換に時間がかかる
    collider: Literal["convex_hull", "convex_decomposition"] = "convex_hull"
    # 物体を左右から挟んで持つとき、反復が少ないと接触を解き切れず指が物体にめり込む
    # （Franka で 45mm の立方体を持つと、8 回では指の間が 23mm まで閉じる。32 回で 44.8mm）
    solver_position_iterations: int = 32
    solver_velocity_iterations: int = 0


class ArmConfig(BaseModel):
    name: str
    joints: list[str]
    ee_link: str  # ユーザが選んだ End Effector link
    ik_body: str  # IK に使う剛体（ee_link が質量なし frame の場合は剛体の祖先）
    ee_offset: Pose = Field(default_factory=Pose)  # ik_body -> ee_link
    tcp_offset: Pose = Field(default_factory=Pose)  # ik_body -> 把持点（TCP）
    approach_axis: list[float] = Field(default_factory=lambda: [0.0, 0.0, 1.0])  # ik_body 座標での接近方向
    finger_axis: list[float] | None = None  # ik_body 座標で指が開閉する方向
    ik_method: Literal["dls", "pinv", "svd", "trans"] = "dls"


class GripperConfig(BaseModel):
    name: str
    arm: str
    joints: list[str]
    leader: str
    # mimic の follower -> [multiplier, offset]。シミュレータでは mimic の拘束を使わず、全関節を
    # open / close の値（follower は multiplier から計算）へ駆動する（import_robot が拘束を無効にする）
    mimic: dict[str, list[float]] = Field(default_factory=dict)
    open: dict[str, float]
    close: dict[str, float]
    # データセット上の gripper 値の定義。記録・変換・評価はすべてこれを参照する
    dataset_scalar: Literal["leader_normalized"] = "leader_normalized"  # 0=閉, 1=開
    # SimReady アセットのグリッパ（urdf2vla.grippers のキー）。URDF のグリッパの代わりに mount_link の mount の位置に
    # Robot Assembler で取り付ける。joints は駆動する関節だけ（追従する関節は PhysX の mimic 拘束で動く）
    simready: str | None = None
    mount_link: str | None = None
    mount: Pose | None = None  # mount_link 座標でのグリッパの base_link（取付面）の姿勢


class JointRoles(BaseModel):
    fixed_at_reset: list[str] = Field(default_factory=list)  # 昇降・首など: reset 時の姿勢で保持
    passive: list[str] = Field(default_factory=list)  # 車輪など: 駆動しない


class ActuatorGroup(BaseModel):
    name: str
    joints: list[str]
    stiffness: float
    damping: float
    effort_limit: dict[str, float] = Field(default_factory=dict)  # 関節ごと（空 = USD の値）
    # 関節ごとの速度上限。mimic から変換した関節は USD に上限が入らないので、URDF（なければ leader）の値を明示する
    velocity_limit: dict[str, float] = Field(default_factory=dict)


class RobotConfig(BaseModel):
    robot_name: str
    asset: AssetConfig
    root_link: str
    base_pose: Pose = Field(default_factory=Pose)
    arms: list[ArmConfig]
    grippers: list[GripperConfig] = Field(default_factory=list)
    joint_roles: JointRoles = Field(default_factory=JointRoles)
    default_pose: dict[str, float]
    joint_limits: dict[str, list[float | None]] = Field(default_factory=dict)
    reset_joint_noise_std: float = 0.0
    actuators: list[ActuatorGroup]
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    @property
    def controlled_joints(self) -> list[str]:
        out: list[str] = []
        for arm in self.arms:
            out += arm.joints
        for g in self.grippers:
            out += g.joints
        return out

    def gripper_for(self, arm: str) -> GripperConfig | None:
        return next((g for g in self.grippers if g.arm == arm), None)
