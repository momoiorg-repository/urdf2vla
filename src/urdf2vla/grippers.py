"""NVIDIA SimReady アセット（Isaac Sim のアセットライブラリ）のグリッパ。

URDF から変換したグリッパは、閉リンクや mimic の表現が importer によって壊れることがある
（CRX-3iA の URDF に含まれる簡略版 Robotiq 2F-85 は、Isaac Sim 6.2 で左 knuckle の可動範囲が効かない）。
物理が整えられた SimReady のグリッパを、Isaac Sim の Robot Assembler で腕に取り付けて使う。

値はアセットから測ったもの（推測で埋めない）。座標はグリッパの base_link（取付面）基準。
"""

from __future__ import annotations

from dataclasses import dataclass, field

ISAAC_ASSETS_ROOT = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2"


@dataclass(frozen=True)
class SimReadyGripper:
    key: str
    title: str
    usd: str  # ISAAC_ASSETS_ROOT からの相対パス
    variants: dict = field(default_factory=dict)  # 参照するときに選ぶ variant
    base_body: str = "base_link"  # 取付面の剛体（Robot Assembler の attachment 側の取付フレーム）
    driven: str = ""  # 駆動する関節（開閉の指令はこの 1 関節）
    open: float = 0.0
    close: float = 0.0
    lower: float = 0.0  # driven の可動範囲 [rad]
    upper: float = 0.0
    followers: tuple[str, ...] = ()  # PhysX の mimic 拘束で driven に追従する関節（駆動しない）
    approach_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)  # base_link 座標で指が伸びる向き
    finger_axis: tuple[float, float, float] = (0.0, 1.0, 0.0)  # base_link 座標で指が開閉する向き
    fingertip: float = 0.0  # base_link 原点から指先までの距離（approach_axis 方向）[m]
    stiffness: float = 200.0
    damping: float = 5.0
    effort: float = 8.0  # N·m（把持力。アセットの maxForce は 26 だが、mimic の反力で手首が押されるので抑える）
    # mimic の拘束で閉リンクを表すグリッパは、ソルバーの反復が少ないと物体を挟んだときに拘束が保てず掴めない
    # （Isaac Lab Arena の DROID も 64）
    solver_position_iterations: int = 64

    @property
    def url(self) -> str:
        return f"{ISAAC_ASSETS_ROOT}/{self.usd}"

    @property
    def joints(self) -> list[str]:
        return [self.driven, *self.followers]


GRIPPERS: dict[str, SimReadyGripper] = {
    "robotiq_2f_85": SimReadyGripper(
        key="robotiq_2f_85",
        title="Robotiq 2F-85",
        usd="Isaac/Robots/Robotiq/2F-85/Robotiq_2F_85_edit.usd",
        variants={"Physics": "Physx_Mimic"},
        driven="finger_joint",
        # 2026-11-05 にアセットで確認: finger_joint の範囲 0〜47°、5 つの関節が PhysxMimicJointAPI で追従、
        # 開いた状態の指の腹は base_link の z 0.091〜0.148、y ±0.0435〜±0.0748（開き幅 87 mm）
        open=0.0,
        close=0.785,  # Isaac Lab Arena の DROID と同じ（π/4）
        lower=0.0,
        upper=0.8203,
        followers=(
            "right_outer_knuckle_joint",
            "left_inner_finger_joint",
            "right_inner_finger_joint",
            "left_inner_finger_knuckle_joint",
            "right_inner_finger_knuckle_joint",
        ),
        approach_axis=(0.0, 0.0, 1.0),
        finger_axis=(0.0, 1.0, 0.0),
        fingertip=0.1484,
    ),
}


def get_gripper(key: str) -> SimReadyGripper:
    if key not in GRIPPERS:
        raise ValueError(f"SimReady のグリッパ '{key}' は未対応です。使えるもの: {', '.join(GRIPPERS)}")
    return GRIPPERS[key]
