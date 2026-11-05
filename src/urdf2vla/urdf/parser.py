"""URDF パーサ（標準ライブラリのみ。Isaac Sim なしで動く）。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MOVABLE_TYPES = ("revolute", "continuous", "prismatic")


class UrdfError(ValueError):
    pass


@dataclass
class Mimic:
    joint: str
    multiplier: float = 1.0
    offset: float = 0.0


@dataclass
class Collision:
    xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mesh: str | None = None
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    box: tuple[float, float, float] | None = None


@dataclass
class Link:
    name: str
    mass: float | None = None  # <inertial> がなければ None
    meshes: list[str] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)
    n_visual: int = 0
    n_collision: int = 0

    @property
    def has_inertial(self) -> bool:
        return self.mass is not None and self.mass > 0.0

    @property
    def is_empty(self) -> bool:
        return not self.has_inertial and self.n_visual == 0 and self.n_collision == 0


@dataclass
class Joint:
    name: str
    type: str
    parent: str
    child: str
    xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis: tuple[float, float, float] = (1.0, 0.0, 0.0)
    lower: float | None = None
    upper: float | None = None
    effort: float | None = None
    velocity: float | None = None
    mimic: Mimic | None = None

    @property
    def movable(self) -> bool:
        return self.type in MOVABLE_TYPES


@dataclass
class UrdfModel:
    name: str
    path: Path
    links: dict[str, Link]
    joints: dict[str, Joint]  # URDF 記述順を保持

    def parent_joint(self, link: str) -> Joint | None:
        for j in self.joints.values():
            if j.child == link:
                return j
        return None

    def child_joints(self, link: str) -> list[Joint]:
        return [j for j in self.joints.values() if j.parent == link]

    @property
    def movable_joints(self) -> list[Joint]:
        return [j for j in self.joints.values() if j.movable]


def _floats(text: str | None, n: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if not text:
        return default
    vals = [float(v) for v in text.split()]
    if len(vals) != n:
        raise UrdfError(f"数値が {n} 個ではありません: '{text}'")
    return tuple(vals)


def _opt_float(el: ET.Element | None, attr: str) -> float | None:
    if el is None or el.get(attr) is None:
        return None
    return float(el.get(attr))


def parse_urdf(path: str | Path) -> UrdfModel:
    path = Path(path)
    if not path.exists():
        raise UrdfError(f"URDF が見つかりません: {path}")
    if path.suffix == ".xacro" or path.name.endswith(".urdf.xacro"):
        raise UrdfError(f"{path.name} は xacro です。先に `xacro {path.name} > robot.urdf` で URDF に展開してください。")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise UrdfError(f"URDF の XML が読めません: {e}") from e
    if root.tag != "robot":
        raise UrdfError(f"ルート要素が <robot> ではありません（<{root.tag}>）")

    links: dict[str, Link] = {}
    for el in root.findall("link"):
        name = el.get("name")
        mass_el = el.find("inertial/mass")
        link = Link(
            name=name,
            mass=_opt_float(mass_el, "value"),
            n_visual=len(el.findall("visual")),
            n_collision=len(el.findall("collision")),
        )
        for mesh in el.iter("mesh"):
            if mesh.get("filename"):
                link.meshes.append(mesh.get("filename"))
        for col in el.findall("collision"):
            origin = col.find("origin")
            c = Collision(
                xyz=_floats(origin.get("xyz") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
                rpy=_floats(origin.get("rpy") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
            )
            mesh = col.find("geometry/mesh")
            box = col.find("geometry/box")
            if mesh is not None:
                c.mesh = mesh.get("filename")
                c.scale = _floats(mesh.get("scale"), 3, (1.0, 1.0, 1.0))
            elif box is not None:
                c.box = _floats(box.get("size"), 3, (0.0, 0.0, 0.0))
            link.collisions.append(c)
        links[name] = link

    joints: dict[str, Joint] = {}
    for el in root.findall("joint"):
        origin = el.find("origin")
        limit = el.find("limit")
        mimic_el = el.find("mimic")
        jtype = el.get("type")
        axis_el = el.find("axis")
        joint = Joint(
            name=el.get("name"),
            type=jtype,
            parent=el.find("parent").get("link"),
            child=el.find("child").get("link"),
            xyz=_floats(origin.get("xyz") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
            rpy=_floats(origin.get("rpy") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
            axis=_floats(axis_el.get("xyz") if axis_el is not None else None, 3, (1.0, 0.0, 0.0)),
            lower=_opt_float(limit, "lower"),
            upper=_opt_float(limit, "upper"),
            effort=_opt_float(limit, "effort"),
            velocity=_opt_float(limit, "velocity"),
        )
        if mimic_el is not None:
            joint.mimic = Mimic(
                joint=mimic_el.get("joint"),
                multiplier=float(mimic_el.get("multiplier", 1.0)),
                offset=float(mimic_el.get("offset", 0.0)),
            )
        if jtype == "continuous":
            joint.lower = joint.upper = None
        joints[joint.name] = joint

    for j in joints.values():
        for end in (j.parent, j.child):
            if end not in links:
                raise UrdfError(f"関節 {j.name} が存在しない link '{end}' を参照しています")
    return UrdfModel(name=root.get("name", path.stem), path=path.resolve(), links=links, joints=joints)
