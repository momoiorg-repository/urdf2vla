"""最小限の剛体変換（純 Python）。関節角 0 の順運動学と、カメラの look-at に使う。

四元数は Isaac Lab 3.0 と同じ (x, y, z, w) の順。設定ファイルのキーも rot_xyzw。
"""

from __future__ import annotations

import math

Vec = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # (x, y, z, w)
IDENTITY: Quat = (0.0, 0.0, 0.0, 1.0)


def quat_from_rpy(r: float, p: float, y: float) -> Quat:
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    cp, sp = math.cos(p / 2), math.sin(p / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def quat_mul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_conj(q: Quat) -> Quat:
    return (-q[0], -q[1], -q[2], q[3])


def quat_rotate(q: Quat, v: Vec) -> Vec:
    x, y, z, _ = quat_mul(quat_mul(q, (*v, 0.0)), quat_conj(q))
    return (x, y, z)


def quat_normalize(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q)  # type: ignore[return-value]


class Transform:
    def __init__(self, pos: Vec = (0.0, 0.0, 0.0), rot: Quat = IDENTITY):
        self.pos = tuple(float(c) for c in pos)
        self.rot = quat_normalize(tuple(float(c) for c in rot))

    def __matmul__(self, other: "Transform") -> "Transform":
        p = quat_rotate(self.rot, other.pos)
        return Transform(tuple(a + b for a, b in zip(self.pos, p)), quat_mul(self.rot, other.rot))

    def inverse(self) -> "Transform":
        inv = quat_conj(self.rot)
        p = quat_rotate(inv, self.pos)
        return Transform((-p[0], -p[1], -p[2]), inv)

    def apply(self, v: Vec) -> Vec:
        p = quat_rotate(self.rot, v)
        return tuple(a + b for a, b in zip(self.pos, p))  # type: ignore[return-value]

    @staticmethod
    def from_xyz_rpy(xyz: Vec, rpy: Vec) -> "Transform":
        return Transform(xyz, quat_from_rpy(*rpy))

    def rounded(self, nd: int = 6) -> dict:
        return {"pos": [round(c, nd) for c in self.pos], "rot_xyzw": [round(c, nd) for c in self.rot]}


def normalize(v: Vec) -> Vec:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        raise ValueError("ゼロベクトルは正規化できません")
    return tuple(c / n for c in v)  # type: ignore[return-value]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def quat_from_matrix(m: list[list[float]]) -> Quat:
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    q = quat_normalize(q)
    return q if q[3] >= 0 else tuple(-c for c in q)  # type: ignore[return-value]


def look_at_ros(eye: Vec, target: Vec, up: Vec = (0.0, 0.0, 1.0)) -> Quat:
    """ROS 光学座標系（+Z 前方, +X 右, +Y 下）のカメラ姿勢を返す（親フレーム基準）。"""
    z = normalize(tuple(t - e for t, e in zip(target, eye)))
    if abs(sum(a * b for a, b in zip(z, normalize(up)))) > 0.999:
        up = (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0)
    x = normalize(cross(z, up))  # 右
    y = cross(z, x)  # 下
    m = [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]
    return quat_from_matrix(m)
