"""VLA interface metadata: VLA から見た observation / action の定義。

記録（teleop 環境）・データ変換・推論（評価環境）はすべてこの定義に従う。
- state  = [arm ごとの関節角..., gripper ごとのスカラー...]
- action = [arm ごとの関節目標（絶対値）..., gripper ごとのスカラー...]
gripper のスカラーは robot_config の dataset_scalar（leader 関節を 0=閉 / 1=開 に正規化）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImageSpec(BaseModel):
    key: str  # データセット上の名前（observation.images.<key>）
    camera: str
    width: int
    height: int


class Group(BaseModel):
    name: str
    kind: Literal["joint_position", "gripper_scalar"]
    joints: list[str] = Field(default_factory=list)  # joint_position のとき
    gripper: str | None = None  # gripper_scalar のとき

    @property
    def dim(self) -> int:
        return len(self.joints) if self.kind == "joint_position" else 1


class StateSpec(BaseModel):
    groups: list[Group]

    @property
    def dimension(self) -> int:
        return sum(g.dim for g in self.groups)

    @property
    def names(self) -> list[str]:
        out: list[str] = []
        for g in self.groups:
            out += g.joints if g.kind == "joint_position" else [f"{g.gripper}.scalar"]
        return out


class ActionSpec(StateSpec):
    type: Literal["joint_position_absolute"] = "joint_position_absolute"
    label_source: Literal["commanded_joint_target"] = "commanded_joint_target"
    gripper_threshold: float = 0.5  # 推論時: スカラー >= 閾値 で開く


class VlaInterface(BaseModel):
    vla_type: Literal["gr00t"] = "gr00t"
    control_hz: float
    images: list[ImageSpec]
    state: StateSpec
    action: ActionSpec
    language_instruction: str
    embodiment_tag: str = "new_embodiment"
    action_horizon: int = 16  # GR00T が 1 回に出す行動の step 数
