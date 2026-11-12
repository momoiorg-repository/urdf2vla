"""URDF に書かれたカメラの frame を見つける（ロボット本来のカメラ: 手先・頭など）。

URDF にはカメラの「種類」を書く決まりがないので、link の名前と木の形から推定する:

- 名前に camera / cam / optical を含む link（mount・support・bracket・holder を含むものは取付部品として除く）
- 深度・赤外・IMU の frame（depth / infra / ir / imu / accel / gyro）は除く（RGB カメラだけを使う）
- 同じ機器の frame（camera_link → camera_center → left_camera_frame → left_camera_frame_optical のような fixed の連鎖）は
  1 つにまとめ、末端の frame を使う。優先順: 光学 frame（optical）> color / rgb > left（ステレオの基準側）
- 向きの約束（REP 103）: 名前に optical を含む frame は z が前方・y が下（Isaac Lab の "ros"）、それ以外は x が前方・z が上
  （Isaac Lab の "world"）
- 取り付け先: カメラから root へ辿る途中に arm の関節があれば、その arm のカメラ（最後の関節より先なら手先）。なければ頭・体

視野角は Gazebo の <sensor type="camera"> の horizontal_fov があれば使う（なければ None。configure_camera が既定値を使う）。
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from urdf2vla.urdf import analysis as A
from urdf2vla.urdf.parser import UrdfModel

_CAMERA = re.compile(r"camera|(^|_)cam(_|$|\d)|optical", re.IGNORECASE)
_MOUNT = re.compile(r"mount|support|bracket|holder", re.IGNORECASE)
_NOT_RGB = re.compile(r"depth|infra|(^|_)ir\d*(_|$)|imu|accel|gyro", re.IGNORECASE)
_DEVICE = re.compile(r"camera|(^|_)cam(_|$|\d)|optical|zed|realsense|oak|(^|_)d4\d\d", re.IGNORECASE)


@dataclass
class UrdfCamera:
    link: str  # カメラの frame（URDF の link 名）
    convention: str  # "ros"（optical: z 前方, y 下）/ "world"（x 前方, z 上）
    arm: str | None = None  # 取り付いている arm の名前（なければ頭・体）
    role: str = "head"  # "wrist"（arm の最後の関節より先）/ "arm"（arm の途中）/ "head"（arm 以外）
    hfov_deg: float | None = None  # Gazebo の horizontal_fov（あれば）
    alternatives: list[str] = field(default_factory=list)  # 同じ機器のほかの frame（選ばなかったもの）

    def to_dict(self) -> dict:
        return {
            "link": self.link,
            "convention": self.convention,
            "arm": self.arm,
            "role": self.role,
            "hfov_deg": self.hfov_deg,
            "alternatives": self.alternatives,
        }


def is_camera_name(name: str) -> bool:
    return bool(_CAMERA.search(name)) and not _MOUNT.search(name)


def _device_root(model: UrdfModel, link: str) -> str:
    """fixed の関節で親へ辿り、名前がカメラの機器らしい（camera・zed・realsense など）間は遡る。いちばん上の link。"""
    cur = link
    while True:
        j = model.parent_joint(cur)
        if j is None or j.type != "fixed" or not _DEVICE.search(j.parent) or _MOUNT.search(j.parent):
            return cur
        cur = j.parent


def _priority(name: str) -> tuple:
    n = name.lower()
    return (
        "optical" in n,
        "color" in n or "rgb" in n,
        "left" in n,
        -len(n),  # 同点なら短い名前
    )


def _gazebo_hfov(urdf_path) -> dict[str, float]:
    """<gazebo reference="link"><sensor type="camera"><camera><horizontal_fov> を link → 度 で返す。"""
    out: dict[str, float] = {}
    try:
        root = ET.parse(str(urdf_path)).getroot()
    except (ET.ParseError, OSError):
        return out
    for gz in root.iter("gazebo"):
        ref = gz.get("reference")
        if not ref:
            continue
        for sensor in gz.iter("sensor"):
            if (sensor.get("type") or "").lower() not in ("camera", "rgbd_camera", "depth", "depth_camera", "multicamera", "wideanglecamera"):
                continue
            el = sensor.find(".//horizontal_fov")
            if el is not None and el.text:
                try:
                    out[ref] = round(math.degrees(float(el.text)), 2)
                except ValueError:
                    pass
                break
    return out


def find_cameras(model: UrdfModel, arms: list[tuple[str, list[str]]]) -> list[UrdfCamera]:
    """URDF のカメラの frame（機器ごとに 1 つ）。arms は (名前, root から先端への関節名) のリスト。

    並びは arm のカメラ（arms の順）→ 頭・体のカメラ。"""
    names = [n for n in model.links if is_camera_name(n) and not _NOT_RGB.search(n)]
    # 末端だけ: 自分の fixed の子孫にもカメラの frame があれば、そちらを使う
    leaves = [n for n in names if not any(d != n and d in names for d in A.fixed_descendants(model, n))]
    groups: dict[str, list[str]] = {}
    for n in leaves:
        groups.setdefault(_device_root(model, n), []).append(n)
    fovs = _gazebo_hfov(model.path)
    cams = []
    for members in groups.values():
        members = sorted(members, key=_priority, reverse=True)
        link = members[0]
        # 視野角: 選んだ frame か、同じ機器の frame（Gazebo のセンサは camera_link などに付いていることが多い）
        device = set(A.fixed_descendants(model, _device_root(model, link))) | {link}
        hfov = fovs.get(link) or next((fovs[d] for d in device if d in fovs), None)
        cam = UrdfCamera(
            link=link,
            convention="ros" if "optical" in link.lower() else "world",
            hfov_deg=hfov,
            alternatives=members[1:],
        )
        # 取り付け先: root までの関節に arm の関節があるか
        path = []
        cur = link
        while (j := model.parent_joint(cur)) is not None:
            path.append(j.name)
            cur = j.parent
        for arm_name, joints in arms:
            hit = [k for k, jn in enumerate(joints) if jn in path]
            if hit:
                cam.arm = arm_name
                cam.role = "wrist" if max(hit) == len(joints) - 1 else "arm"
                break
        cams.append(cam)
    order = {name: i for i, (name, _) in enumerate(arms)}
    cams.sort(key=lambda c: (c.arm is None, order.get(c.arm, 0), c.link))
    return cams


def default_camera_names(cams: list[UrdfCamera], arm_count: int) -> list[str]:
    """カメラの名前: 手先は <arm>_wrist_cam（単腕は wrist_cam）、頭・体は head_cam（2 台目から head_cam_2 ...）。"""
    out, used = [], set()
    for c in cams:
        if c.role == "wrist":
            base = "wrist_cam" if arm_count == 1 else f"{c.arm}_wrist_cam"
        elif c.role == "arm":
            base = f"{c.arm}_arm_cam"
        else:
            base = "head_cam"
        name, k = base, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        out.append(name)
    return out
