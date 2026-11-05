"""メッシュパスの解決と、シミュレータに渡す正規化 URDF の生成。

- package://<pkg>/... は URDF の親ディレクトリを遡って <pkg>（package.xml の name 一致）を探す
- 絶対パスはホストにそのまま無ければ、コンテナ側パスとみなして runtime.yaml の mounts で解決し、
  それでも無ければ末尾の部分パスで URDF の親ディレクトリと package_paths の下を探す
- 正規化 URDF では全メッシュをシミュレータから見える絶対パスに書き換え、空の root link（world）を外す
"""

from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from urdf2vla.runtime.config import Runtime
from urdf2vla.schema.robot import MeshReport
from urdf2vla.urdf.parser import UrdfModel

PKG_RE = re.compile(r"^package://([^/]+)/(.*)$")


def _find_package(start: Path, pkg: str, extra: list[Path]) -> Path | None:
    candidates: list[Path] = []
    for d in [start, *start.parents]:
        candidates += [d, d / pkg]
        if d == d.parent:
            break
    candidates += extra + [p / pkg for p in extra]
    for env_path in os.environ.get("ROS_PACKAGE_PATH", "").split(":"):
        if env_path:
            candidates += [Path(env_path), Path(env_path) / pkg]
    for c in candidates:
        xml = c / "package.xml"
        if xml.exists():
            try:
                name = ET.parse(xml).getroot().findtext("name")
            except ET.ParseError:
                continue
            if name and name.strip() == pkg:
                return c
        elif c.name == pkg and c.is_dir() and (c / "meshes").is_dir():
            return c
    return None


def resolve_mesh(filename: str, urdf_dir: Path, runtime: Runtime, package_paths: list[Path]) -> str | None:
    """メッシュ参照をホスト上の実ファイルパスに解決する（見つからなければ None）。"""
    m = PKG_RE.match(filename)
    if m:
        pkg_dir = _find_package(urdf_dir, m.group(1), package_paths)
        if pkg_dir is None:
            return None
        p = pkg_dir / m.group(2)
        return str(p) if p.exists() else None
    if filename.startswith("file://"):
        filename = filename[len("file://") :]
    p = Path(filename)
    if not p.is_absolute():
        p = urdf_dir / p
        return str(p.resolve()) if p.exists() else None
    if p.exists():
        return str(p)
    mapped = runtime.to_host(str(p))
    if mapped and Path(mapped).exists():
        return mapped
    return _find_by_suffix(p, [urdf_dir, *list(urdf_dir.parents)[:4], *package_paths])


def _find_by_suffix(p: Path, bases: list[Path]) -> str | None:
    """別のマシン（コンテナ）の絶対パスを、末尾の部分パスで bases の下から探す。
    例: /workspaces/x/ros2_ws/src/pkg/meshes/a.dae → <URDF の親>/ros2_ws/src/pkg/meshes/a.dae"""
    parts = p.parts[1:]
    for i in range(len(parts) - 1):  # 長い部分パスから（ファイル名だけでは一致させない）
        tail = Path(*parts[i:])
        for base in bases:
            c = base / tail
            if c.exists():
                return str(c)
    return None


def mesh_report(model: UrdfModel, runtime: Runtime, package_paths: list[Path]) -> MeshReport:
    report = MeshReport()
    for link in model.links.values():
        for mesh in link.meshes:
            report.total += 1
            if mesh in report.resolved or mesh in report.unresolved:
                continue
            host = resolve_mesh(mesh, model.path.parent, runtime, package_paths)
            if host:
                report.resolved[mesh] = host
            else:
                report.unresolved.append(mesh)
    return report


def write_normalized_urdf(
    model: UrdfModel, out_path: Path, runtime: Runtime, report: MeshReport, drop_root: str | None, drop_links: set[str] | None = None
) -> None:
    """メッシュをシミュレータ側の絶対パスに書き換えた URDF を out_path に書く。

    シミュレータ（コンテナ）から見えない場所のメッシュは、そのディレクトリごと out_path の隣の meshes/ に
    コピーして参照する（URDF がどこにあっても runtime.yaml の mounts を編集せずに使えるように）。
    """
    tree = ET.parse(model.path)
    root = tree.getroot()
    copied: dict[Path, Path] = {}
    for mesh in root.iter("mesh"):
        fn = mesh.get("filename")
        if fn in report.resolved:
            mesh.set("filename", _visible_path(Path(report.resolved[fn]), out_path.parent / "meshes", runtime, copied))
    if drop_links:  # SimReady のグリッパに付け替えるとき、URDF のグリッパの link と関節を外す
        for el in list(root.findall("link")):
            if el.get("name") in drop_links:
                root.remove(el)
        for el in list(root.findall("joint")):
            if el.find("child").get("link") in drop_links:
                root.remove(el)
    if drop_root:
        for el in list(root.findall("link")):
            if el.get("name") == drop_root:
                root.remove(el)
        for el in list(root.findall("joint")):
            if el.find("parent").get("link") == drop_root:
                root.remove(el)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def _visible_path(host_file: Path, copy_root: Path, runtime: Runtime, copied: dict[Path, Path]) -> str:
    """host_file をシミュレータから見えるパスにする。見えなければディレクトリごと copy_root にコピーする
    （テクスチャなど同じディレクトリの相対参照を保つため）。"""
    try:
        return runtime.to_runtime(host_file)
    except ValueError:
        pass
    src_dir = host_file.parent
    if src_dir not in copied:
        dst = copy_root / f"{len(copied):02d}_{src_dir.name}"
        shutil.copytree(src_dir, dst, dirs_exist_ok=True)
        copied[src_dir] = dst
    return runtime.to_runtime(copied[src_dir] / host_file.name)
