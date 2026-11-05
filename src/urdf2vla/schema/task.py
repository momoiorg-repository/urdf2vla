"""Pick & Place タスク定義。

物体は候補のリスト（`objects`）で、エピソードごとに 1 つを選んで台の上に置き、指示文でその名前を使う。
各物体はプリミティブの箱（`asset` なし。ダウンロード不要）か、USD のアセット（urdf2vla.objects のカタログ）。
置き場所（`target`）は台の上の円（`asset` なし）か、トレイなどの容器（USD のアセット、動かない）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TableConfig(BaseModel):
    center: list[float]  # [x, y]（ロボット base 基準）
    size: list[float]  # [x, y, 厚さ]
    top_z: float  # 天板上面の高さ


class ObjectConfig(BaseModel):
    key: str = "cube"  # カタログの key（"cube" はプリミティブの箱）
    name: str = "red cube"  # 指示文で使う名前
    asset: str | None = None  # USD の URL（なし = プリミティブの箱）
    variants: dict[str, str] = Field(default_factory=dict)
    scale: float = 1.0
    size: list[float] = Field(default_factory=lambda: [0.045, 0.045, 0.045])  # 縮尺を掛けた外接箱 [m]
    bottom: float | None = None  # 原点から外接箱の底までの高さ（縮尺後）。なし = 原点が中心（プリミティブ）
    mass: float = 0.1  # プリミティブのみ（アセットはアセットの質量）
    box: bool = True  # 箱の形（向かい合う平らな面がある）。指の開きが幅と合うかの検査はこのときだけ
    color: list[float] = Field(default_factory=lambda: [0.85, 0.2, 0.15])  # プリミティブのみ

    @property
    def bottom_z(self) -> float:
        return self.bottom if self.bottom is not None else -self.size[2] / 2

    @property
    def center_z(self) -> float:
        """原点から外接箱の中心までの高さ。"""
        return self.bottom_z + self.size[2] / 2

    @property
    def grasp_width(self) -> float:
        return min(self.size[0], self.size[1])


class TargetConfig(BaseModel):
    pos: list[float]  # [x, y]
    key: str = "disc"  # カタログの key（"disc" は台の上の円）
    name: str = "green target"
    asset: str | None = None  # USD の URL（なし = 円）
    variants: dict[str, str] = Field(default_factory=dict)
    scale: float = 1.0
    size: list[float] = Field(default_factory=lambda: [0.12, 0.12, 0.002])  # 外接箱 [m]（円は直径）
    bottom: float = 0.0
    radius: float = 0.06  # 円の半径
    color: list[float] = Field(default_factory=lambda: [0.15, 0.6, 0.25])

    @property
    def is_container(self) -> bool:
        return self.asset is not None


class SuccessConfig(BaseModel):
    radius: float = 0.06  # 円: 物体中心と目標中心の水平距離
    container_margin: float = 0.01  # 容器: 物体の中心が縁からこれ以上内側にあること [m]
    max_height_above_table: float = 0.03  # 置かれている（持ち上げ中でない）判定（容器は容器の高さの上から）
    require_release: bool = True


class TaskConfig(BaseModel):
    task_type: Literal["pick_place"] = "pick_place"
    table: TableConfig
    objects: list[ObjectConfig]
    spawn_x: list[float]  # 物体を置く範囲（エピソードごとにこの中でばらつかせる）
    spawn_y: list[float]
    target: TargetConfig
    success: SuccessConfig = Field(default_factory=SuccessConfig)
    # arm ごとの「作業の構え」の TCP 位置（env 座標）。初期姿勢の IK 提案の目標に使う
    home_tcp: dict[str, list[float]] = Field(default_factory=dict)
    # 指示文。{object} と {target} はエピソードで選んだ物体と置き場所の名前になる
    instruction: str = "pick up the {object} and place it on the {target}"
    episode_length_s: float = 30.0
    sim_dt: float = 0.01
    decimation: int = 5
    # RTX カメラが env.step 内の描画で更新されない環境向けの対策（既定は不要）。有効にすると 1 制御 step ごとに物理が 1 step 余分に進む
    render_fix: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_single_object(cls, data):
        """旧形式（object: 1 つ、language_instruction）の task.yaml を読む。"""
        if isinstance(data, dict) and "object" in data and "objects" not in data:
            data = dict(data)
            obj = dict(data.pop("object"))
            data.setdefault("spawn_x", obj.pop("spawn_x", None))
            data.setdefault("spawn_y", obj.pop("spawn_y", None))
            obj.setdefault("key", "cube")
            obj["name"] = obj.get("name") if obj.get("name") not in (None, "cube") else "red cube"
            data["objects"] = [obj]
            if "language_instruction" in data:
                data["instruction"] = data.pop("language_instruction")
        return data

    def instruction_for(self, index: int) -> str:
        return self.instruction.format(object=self.objects[index].name, target=self.target.name)

    @property
    def language_instruction(self) -> str:
        """最初の物体の指示文（データセット全体の既定値）。"""
        return self.instruction_for(0)

    @property
    def physics_steps_per_control(self) -> int:
        return self.decimation + (1 if self.render_fix else 0)

    @property
    def control_hz(self) -> float:
        """実効の制御周期（データセットの fps はこの値）。"""
        return 1.0 / (self.sim_dt * self.physics_steps_per_control)
