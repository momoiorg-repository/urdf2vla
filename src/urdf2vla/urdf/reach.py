"""手先を真下に向けたまま届くか（ホスト側の小さな数値 IK。configure_task が台の位置を決めるのに使う）。

腕の長さだけで台を置くと、腕が短くグリッパが長いロボット（例: CRX-3iA + Robotiq 2F-85。真下に向けると
グリッパの長さの分だけ手首が高くなる）では作業域の奥や上に届かない。ここでは URDF の関節の連鎖と TCP の
オフセットから、目標の位置に手先（approach_axis）を真下に向けて置けるかを、減衰最小二乗法で調べる。
numpy を使わない（ホストの依存を増やさない）。関節は 6〜7 個なので純 Python で十分速い。
"""

from __future__ import annotations

import math
import random

from urdf2vla.urdf import analysis as A
from urdf2vla.urdf.kinematics import Transform

DOWN = (0.0, 0.0, -1.0)
POS_TOL = 0.003  # [m]
ANGLE_TOL = math.radians(3.0)
ROT_WEIGHT = 0.2  # 向きの誤差（単位ベクトルの差）を位置の誤差 [m] と比べる重み


def _solve(m: list[list[float]], b: list[float]) -> list[float]:
    """小さな正方の連立一次方程式（部分ピボットのガウス消去）。"""
    n = len(b)
    a = [row[:] + [b[i]] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        if abs(a[c][c]) < 1e-12:
            continue
        for r in range(c + 1, n):
            f = a[r][c] / a[c][c]
            for k in range(c, n + 1):
                a[r][k] -= f * a[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = a[r][n] - sum(a[r][k] * x[k] for k in range(r + 1, n))
        x[r] = s / a[r][r] if abs(a[r][r]) > 1e-12 else 0.0
    return x


class ReachChecker:
    """1 本の腕について、env 座標の点に手先を真下に向けて届くかを調べる。"""

    def __init__(self, model, cfg, arm, seed: int = 0):
        self.model, self.arm = model, arm
        self.base = Transform(tuple(cfg.base_pose.pos), tuple(cfg.base_pose.rot_xyzw))
        self.root = cfg.root_link
        self.tcp = Transform(tuple(arm.tcp_offset.pos), tuple(arm.tcp_offset.rot_xyzw))
        self.joints = list(arm.joints)
        self.lo, self.hi = [], []
        for n in self.joints:
            j = model.joints[n]
            lo = j.lower if j.lower is not None else -math.pi
            hi = j.upper if j.upper is not None else math.pi
            self.lo.append(max(lo, -2 * math.pi))
            self.hi.append(min(hi, 2 * math.pi))
        rng = random.Random(seed)
        start = [float((cfg.default_pose or {}).get(n, 0.0)) for n in self.joints]
        mid = [(a + b) / 2 for a, b in zip(self.lo, self.hi)]
        self.seeds = [start, [0.0] * len(self.joints), mid]
        self.seeds += [[rng.uniform(max(a, -math.pi), min(b, math.pi)) for a, b in zip(self.lo, self.hi)] for _ in range(6)]
        self._last: list[float] | None = None  # 直前に解けた姿勢（隣の点の初期値に使う）

    def _fk(self, q: list[float]) -> tuple[tuple, tuple]:
        t = self.base @ A.relative_transform(self.model, self.root, self.arm.ik_body, dict(zip(self.joints, q)))
        p = (t @ self.tcp).pos
        a = Transform((0, 0, 0), t.rot).apply(tuple(self.arm.approach_axis))
        return p, a

    def _residual(self, q, target) -> list[float]:
        p, a = self._fk(q)
        return [target[i] - p[i] for i in range(3)] + [ROT_WEIGHT * (DOWN[i] - a[i]) for i in range(3)]

    def _ik(self, target, q, iters: int = 150, damping: float = 0.03) -> list[float] | None:
        q = [min(max(v, lo), hi) for v, lo, hi in zip(q, self.lo, self.hi)]
        n = len(q)
        for _ in range(iters):
            r = self._residual(q, target)
            pos_err = math.sqrt(sum(v * v for v in r[:3]))
            ang = 2 * math.asin(min(1.0, math.sqrt(sum(v * v for v in r[3:])) / ROT_WEIGHT / 2))
            if pos_err < POS_TOL and ang < ANGLE_TOL:
                return q
            eps = 1e-5
            cols = []
            for k in range(n):
                dq = q[:]
                dq[k] += eps
                r2 = self._residual(dq, target)
                cols.append([(r[i] - r2[i]) / eps for i in range(6)])  # d(位置)/dq（残差は目標 - 現在なので符号を返す）
            jjt = [[sum(cols[k][i] * cols[k][j] for k in range(n)) + (damping**2 if i == j else 0.0) for j in range(6)] for i in range(6)]
            y = _solve(jjt, r)
            step = [sum(cols[k][i] * y[i] for i in range(6)) for k in range(n)]
            q = [min(max(v + s, lo), hi) for v, s, lo, hi in zip(q, step, self.lo, self.hi)]
        return None

    def reachable(self, target) -> bool:
        for seed in ([self._last] if self._last else []) + self.seeds:
            q = self._ik(target, seed)
            if q is not None:
                self._last = q
                return True
        return False
