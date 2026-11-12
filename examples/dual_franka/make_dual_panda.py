"""Franka（examples/franka の panda_arm_hand.urdf）を 2 本、共通の台座に y = ±SPACING で並べた双腕の URDF を作る。

双腕の流れ（検査・収集・変換・replay）をダウンロードなしで確かめるための合成ロボット。左右の link・関節の名前に
left_ / right_ を付ける。2 本の腕をつなぐ台座（base）には質量を持たせる（質量のない root link だと Isaac Sim の importer が
腕ごとに別の articulation に分け、import_robot が "Expected 1 prims at '/World/Robot', found 2" で止まる）。

  python examples/dual_franka/make_dual_panda.py            # → examples/franka/franka_description/robots/dual_panda.urdf
  python examples/dual_franka/make_dual_panda.py --spacing 0.3
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

ROBOTS = Path(__file__).resolve().parents[1] / "franka" / "franka_description" / "robots"


def make(src: Path, dst: Path, spacing: float) -> None:
    arm = ET.parse(src).getroot()
    out = ET.Element("robot", name="dual_panda")
    base = ET.SubElement(out, "link", name="base")
    inertial = ET.SubElement(base, "inertial")
    ET.SubElement(inertial, "origin", xyz="0 0 -0.05", rpy="0 0 0")
    ET.SubElement(inertial, "mass", value="5.0")
    ET.SubElement(inertial, "inertia", ixx="0.1", ixy="0", ixz="0", iyy="0.1", iyz="0", izz="0.1")
    visual = ET.SubElement(base, "visual")
    ET.SubElement(visual, "origin", xyz="0 0 -0.05", rpy="0 0 0")
    ET.SubElement(ET.SubElement(visual, "geometry"), "box", size=f"0.2 {2 * spacing + 0.2:g} 0.1")
    for side, y in (("left", spacing), ("right", -spacing)):
        for el in arm:
            if el.tag not in ("link", "joint"):
                continue
            e = ET.fromstring(ET.tostring(el))
            e.set("name", f"{side}_{e.get('name')}")
            if e.tag == "joint":
                for k in ("parent", "child"):
                    n = e.find(k)
                    n.set("link", f"{side}_{n.get('link')}")
                m = e.find("mimic")
                if m is not None:
                    m.set("joint", f"{side}_{m.get('joint')}")
            out.append(e)
        j = ET.SubElement(out, "joint", name=f"{side}_mount", type="fixed")
        ET.SubElement(j, "origin", xyz=f"0 {y:g} 0", rpy="0 0 0")
        ET.SubElement(j, "parent", link="base")
        ET.SubElement(j, "child", link=f"{side}_panda_link0")
    ET.indent(out)
    ET.ElementTree(out).write(dst, xml_declaration=True, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spacing", type=float, default=0.35, help="左右の腕の y 方向の位置 [m]（±）")
    ap.add_argument("--out", type=Path, default=ROBOTS / "dual_panda.urdf")
    a = ap.parse_args()
    make(ROBOTS / "panda_arm_hand.urdf", a.out, a.spacing)
    print(a.out)
