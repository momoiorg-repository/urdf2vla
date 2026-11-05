"""Camera metadata。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from urdf2vla.schema.quat import accept_legacy_wxyz


class CameraConfig(BaseModel):
    name: str
    mount: Literal["link", "world"]
    parent_link: str | None = None  # mount=link のときユーザが指定した link
    mount_body: str | None = None  # 実際に取り付ける剛体（parent_link が質量なし frame なら剛体の祖先）
    pos: list[float]  # mount_body（world の場合は env 原点）基準
    rot_xyzw: list[float]  # (x, y, z, w)
    convention: Literal["ros", "opengl", "world"] = "ros"
    width: int = 256
    height: int = 256
    fps: float | None = None  # None = 制御周期と同じ
    focal_length: float = 24.0
    horizontal_aperture: float = 20.955
    intrinsic_matrix: list[float] | None = None  # 実機の camera_info K（row-major 9 要素）があれば優先
    clipping_range: list[float] = Field(default_factory=lambda: [0.01, 10.0])
    data_types: list[str] = Field(default_factory=lambda: ["rgb"])
    preset: str | None = None

    _legacy = model_validator(mode="before")(accept_legacy_wxyz)


class CamerasConfig(BaseModel):
    cameras: list[CameraConfig]
