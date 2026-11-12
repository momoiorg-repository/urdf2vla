"""`urdf2vla view` のカメラ編集の計算（viser に依存しない部分。テストできるように分けてある）。

画面ではすべてのカメラを光学 frame（ros: z が前方・y が下。viser のカメラと同じ）で扱い、保存するときは
取り付け先の link から見た光学 frame の姿勢を ros の約束で書く。触っていないカメラは元の指定（preset など）のまま保存する。
"""

from __future__ import annotations

import math

from urdf2vla.urdf.kinematics import Transform, look_at_ros, quat_from_matrix

APERTURE = 20.955  # 水平の撮像面の幅（Isaac Sim の既定）。視野角は焦点距離で決める

# カメラの frame から光学 frame（ros）への回転。Isaac Lab の CameraCfg.OffsetCfg.convention と同じ約束
#   ros: z 前方・y 下 / opengl: -z 前方・y 上 / world: x 前方・z 上（REP 103 の camera_link）
TO_OPTICAL = {
    "ros": (0.0, 0.0, 0.0, 1.0),
    "opengl": (1.0, 0.0, 0.0, 0.0),  # x 軸まわりに 180°
    "world": quat_from_matrix([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]),  # 光学の x = -y, y = -z, z = x
}


def optical_local(cam: dict) -> Transform:
    """cameras.yaml の 1 台: 取り付け先（mount_body、world なら env 原点）から見た光学 frame の姿勢。"""
    return Transform(tuple(cam["pos"]), tuple(cam["rot_xyzw"])) @ Transform((0.0, 0.0, 0.0), TO_OPTICAL[cam.get("convention", "ros")])


def hfov_deg(cam: dict) -> float:
    return math.degrees(2 * math.atan(cam.get("horizontal_aperture", APERTURE) / (2 * cam.get("focal_length", 24.0))))


def focal_for_hfov(hfov: float, aperture: float = APERTURE) -> float:
    return round(aperture / (2 * math.tan(math.radians(hfov) / 2)), 4)


def vfov_rad(hfov: float, width: int, height: int) -> float:
    return 2 * math.atan(math.tan(math.radians(hfov) / 2) * height / width)


def relative_to(mount_world: Transform | None, cam_world: Transform) -> Transform:
    """world の姿勢 → 取り付け先から見た姿勢（mount_world が None なら world 固定）。"""
    return cam_world if mount_world is None else mount_world.inverse() @ cam_world


def original_spec(cam: dict, arm_index: dict[str, int]) -> dict:
    """触っていないカメラを保存するときの指定: preset のカメラは preset のまま（台の配置などが変われば追従する）。
    arm_index は ik_body（= 手先カメラの mount_body）→ arm の番号。"""
    preset = cam.get("preset")
    if preset == "wrist":
        spec = {"preset": "wrist", "name": cam["name"]}
        if cam.get("mount_body") in arm_index:
            spec["arm"] = arm_index[cam["mount_body"]]
        return spec
    if preset in ("left", "right", "front", "side"):
        return {"preset": preset, "name": cam["name"]}
    if preset == "urdf":
        return {"preset": "urdf", "link": cam["parent_link"], "name": cam["name"], "hfov_deg": round(hfov_deg(cam), 2),
                "width": cam["width"], "height": cam["height"]}  # fmt: skip
    return explicit_spec(cam["name"], cam.get("mount_body") if cam["mount"] == "link" else None, optical_local(cam), hfov_deg(cam), cam["width"], cam["height"],
                         clipping_range=cam.get("clipping_range"))  # fmt: skip


def explicit_spec(name: str, mount_link: str | None, local_optical: Transform, hfov: float, width: int, height: int,
                  clipping_range: list[float] | None = None) -> dict:
    """画面で動かしたカメラの指定（project.yaml の cameras の明示指定と同じ形）。clipping_range は元のカメラのもの
    （URDF のカメラは手前 3 cm を描かない。動かしても筐体の中から写すことがあるので引き継ぐ）。"""
    spec = {"name": name, "mount": "world" if mount_link is None else "link"}
    if mount_link is not None:
        spec["parent_link"] = mount_link
    spec.update(
        pos=[round(c, 5) for c in local_optical.pos],
        rot_xyzw=[round(c, 6) for c in local_optical.rot],
        convention="ros",
        width=int(width),
        height=int(height),
        focal_length=focal_for_hfov(hfov),
        horizontal_aperture=APERTURE,
    )
    if clipping_range:
        spec["clipping_range"] = [round(float(v), 4) for v in clipping_range]
    return spec


def new_world_camera(table_center: tuple[float, float], top_z: float, span: float = 0.6) -> Transform:
    """「カメラを追加」の初期位置: 台の正面の上から台の中央を見下ろす（光学 frame）。"""
    cx, cy = table_center
    eye = (cx + 0.9 * span, cy, top_z + 0.8 * span)
    return Transform(eye, look_at_ros(eye, (cx, cy, top_z)))
