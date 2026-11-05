"""Pick & Place タスク定義（シーンはオフラインで動くようにプリミティブのみで構成する）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TableConfig(BaseModel):
    center: list[float]  # [x, y]（ロボット base 基準）
    size: list[float]  # [x, y, 厚さ]
    top_z: float  # 天板上面の高さ


class ObjectConfig(BaseModel):
    name: str = "cube"
    size: list[float] = Field(default_factory=lambda: [0.045, 0.045, 0.045])
    mass: float = 0.1
    color: list[float] = Field(default_factory=lambda: [0.85, 0.2, 0.15])
    spawn_x: list[float]
    spawn_y: list[float]


class TargetConfig(BaseModel):
    pos: list[float]  # [x, y]
    radius: float = 0.06
    color: list[float] = Field(default_factory=lambda: [0.15, 0.6, 0.25])


class SuccessConfig(BaseModel):
    radius: float = 0.06  # 物体中心と目標中心の水平距離
    max_height_above_table: float = 0.03  # 置かれている（持ち上げ中でない）判定
    require_release: bool = True


class TaskConfig(BaseModel):
    task_type: Literal["pick_place"] = "pick_place"
    table: TableConfig
    object: ObjectConfig
    target: TargetConfig
    success: SuccessConfig = Field(default_factory=SuccessConfig)
    # arm ごとの「作業の構え」の TCP 位置（env 座標）。初期姿勢の IK 提案の目標に使う
    home_tcp: dict[str, list[float]] = Field(default_factory=dict)
    language_instruction: str = "pick up the red cube and place it on the green target"
    episode_length_s: float = 30.0
    sim_dt: float = 0.01
    decimation: int = 5
    # RTX カメラが env.step 内の描画で更新されない環境向けの対策（既定は不要: Isaac Lab 2.3.2 + RTX 5080 で
    # 120 step 中の凍結 0 回を確認）。有効にすると 1 制御 step ごとに物理が 1 step 余分に進む
    render_fix: bool = False

    @property
    def physics_steps_per_control(self) -> int:
        return self.decimation + (1 if self.render_fix else 0)

    @property
    def control_hz(self) -> float:
        """実効の制御周期（データセットの fps はこの値）。"""
        return 1.0 / (self.sim_dt * self.physics_steps_per_control)
