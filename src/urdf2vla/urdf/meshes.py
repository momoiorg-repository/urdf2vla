"""衝突メッシュの頂点読み込み（STL / OBJ。指先の位置や向きを求めるのに使う）。"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=256)
def stl_vertices(path: str) -> tuple[tuple[float, float, float], ...] | None:
    """バイナリ / ASCII STL の頂点。読めなければ None。"""
    p = Path(path)
    if p.suffix.lower() != ".stl" or not p.exists():
        return None
    data = p.read_bytes()
    if len(data) >= 84:
        (n,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * n:  # バイナリ（ヘッダが "solid" で始まるものもあるので長さで判定）
            out = []
            for i in range(n):
                vals = struct.unpack_from("<12f", data, 84 + 50 * i)
                out += [vals[3:6], vals[6:9], vals[9:12]]
            return tuple(out)
    text = data.decode(errors="ignore")
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == "vertex":
            out.append(tuple(float(v) for v in parts[1:]))
    return tuple(out) or None


@lru_cache(maxsize=1024)
def obj_vertices(path: str) -> tuple[tuple[float, float, float], ...] | None:
    p = Path(path)
    if p.suffix.lower() != ".obj" or not p.exists():
        return None
    out = []
    for line in p.read_text(errors="ignore").splitlines():
        if line.startswith("v "):
            parts = line.split()
            out.append((float(parts[1]), float(parts[2]), float(parts[3])))
    return tuple(out) or None


def mesh_vertices(path: str) -> tuple[tuple[float, float, float], ...] | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".stl":
        return stl_vertices(path)
    if suffix == ".obj":
        return obj_vertices(path)
    return None
