"""Pick & Place の物体と置き場所（トレイ）のカタログ。

物体は NVIDIA の SimReady アセット（Isaac Sim 6.2 のアセットライブラリ）と Isaac Lab Arena の物体ライブラリから選ぶ。
値はアセットを Isaac Sim で読み込んで測ったもの（推測で埋めない）。大きさは Isaac Lab が縮尺 1 で生成したときの軸平行の外接箱 [m]
（アセットの根に縮尺が書いてあっても、Isaac Lab は生成時の縮尺で置き換える）、
bottom はアセットの原点から見た外接箱の底の高さ（SimReady は原点が底、Isaac の Props は原点が中心）。

  `task.objects` に key を並べると、エピソードごとにその中から 1 つを選び、指示文でその名前を使う
  （例: "put the apple in the tray"）。"cube" は従来のプリミティブの立方体（ダウンロード不要）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

ISAAC_ROOT = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2/Isaac"
SIMREADY_PHYSX = {"PhysX": "Enabled"}  # Isaac Sim 6.2 の SimReady は衝突形状・質量をこの variant に持つ


@dataclass(frozen=True)
class CatalogAsset:
    key: str
    name: str  # 指示文で使う名前
    usd: str  # ISAAC_ROOT からの相対パス
    size: tuple[float, float, float]  # 縮尺 1 での外接箱 [m]
    bottom: float  # 縮尺 1 での、原点から外接箱の底までの高さ [m]
    mass: float  # アセットの質量 [kg]（参考値。シミュレーションではアセットの値を使う）
    scale: float = 1.0
    variants: dict = field(default_factory=dict)
    source: str = "NVIDIA SimReady (Isaac Sim 6.2)"
    box: bool = False  # 箱の形（向かい合う平らな面がある）か

    @property
    def url(self) -> str:
        return f"{ISAAC_ROOT}/{self.usd}"

    @property
    def scaled_size(self) -> list[float]:
        return [round(v * self.scale, 4) for v in self.size]

    @property
    def scaled_bottom(self) -> float:
        return round(self.bottom * self.scale, 4)

    @property
    def grasp_width(self) -> float:
        """上から掴むときに指の間に入る幅（水平の短い方の辺）。"""
        return min(self.scaled_size[0], self.scaled_size[1])


OBJECTS: dict[str, CatalogAsset] = {
    "rubiks_cube": CatalogAsset(
        "rubiks_cube", "Rubik's cube", "IsaacLab/Arena/assets/object_library/srl_robolab_assets/objects/hot3d/rubiks_cube.usd",
        size=(0.0583, 0.0578, 0.058), bottom=-0.0314, mass=0.2, source="Isaac Lab Arena object library (HOT3D)", box=True,
    ),
    "apple": CatalogAsset(
        "apple", "apple", "SimReady/Residential/Kitchen/Food/Fruit/Red_Delicious_Apple_A01/sm_food_produce_apple_redDelicous_a01_01.usd",
        size=(0.0776, 0.081, 0.0954), bottom=0.0, mass=0.2328, scale=0.7, variants=SIMREADY_PHYSX,
    ),  # 縮尺 1 では幅 7.8 cm で、開き 8 cm の Franka の指に入らない。0.8（6.3 cm）でも片側 8 mm の余裕しかなく、
    # 置いた位置が数 mm ずれると開ループの再生で指が当たる。小ぶりのりんご（約 5.5 cm）にする
    "lime": CatalogAsset(
        "lime", "lime", "SimReady/Residential/Kitchen/Food/Fruit/Lime_A01/sm_food_produce_lime_a01_01.usd",
        size=(0.0636, 0.0748, 0.0612), bottom=0.0, mass=0.2458, variants=SIMREADY_PHYSX,
    ),
    "pear": CatalogAsset(
        "pear", "pear", "SimReady/Residential/Kitchen/Food/Fruit/Pear_A01/sm_food_produce_pear_a01_01.usd",
        size=(0.0609, 0.1335, 0.0622), bottom=0.0, mass=0.1869, variants=SIMREADY_PHYSX,
    ),
    "color_cube": CatalogAsset(
        "color_cube", "colored cube", "Props/Blocks/MultiColorCube/multi_color_cube_instanceable.usd",
        # アセットの根の prim に縮尺 0.77 が書いてあり、Isaac Lab が生成時の縮尺（既定 1）で置き換えるので、生成すると 6.5 cm に
        # なる（launch_sim の asset_size で検出）。カタログの大きさは Isaac Lab が縮尺 1 で生成したときの値、縮尺で 5 cm に戻す
        size=(0.065, 0.065, 0.065), bottom=-0.0325, mass=0.0709, scale=0.77, source="Isaac Sim props", box=True,
    ),
}

CONTAINERS: dict[str, CatalogAsset] = {
    "klt_bin": CatalogAsset(
        "klt_bin", "bin", "Props/KLT_Bin/small_KLT.usd",
        size=(0.198, 0.297, 0.146), bottom=-0.073, mass=0.0, source="Isaac Sim props",
    ),  # 工場の部品箱（KLT）。内寸に物体が入り、縁の高さ 14.6 cm
    "plastic_tray": CatalogAsset(
        "plastic_tray", "tray", "SimReady/Residential/Kitchen/Dishware/Plastic_Tray_A01/sm_dishware_plastic_tray_a01_01.usd",
        size=(0.2557, 0.27, 0.0213), bottom=0.0004, mass=0.4149, variants=SIMREADY_PHYSX,
    ),
}

# configure_task の既定: エピソードごとにこの中から 1 つ（グリッパの開きに入らないものは除く）をトレイに入れる
# rubiks_cube は使えるが既定には入れない（Arena のライブラリの物体で、元のデータセット（HOT3D）の利用条件を確かめていない）
DEFAULT_OBJECTS = ("apple", "lime", "pear", "color_cube")
DEFAULT_CONTAINER = "klt_bin"


def get_object(key: str) -> CatalogAsset:
    if key not in OBJECTS:
        raise KeyError(f"未知の物体 '{key}'（使えるもの: cube, {', '.join(OBJECTS)}）")
    return OBJECTS[key]


def get_container(key: str) -> CatalogAsset:
    if key not in CONTAINERS:
        raise KeyError(f"未知の置き場所 '{key}'（使えるもの: disc, {', '.join(CONTAINERS)}）")
    return CONTAINERS[key]
