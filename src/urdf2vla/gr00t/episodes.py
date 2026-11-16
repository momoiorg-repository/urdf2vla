"""GR00T のデータセットに書くエピソードの末尾の扱いと、グリッパの開閉のタイミングの検査（純 Python）。

Isaac-GR00T の学習は、行動の列（action_horizon step）が収まる step（0..T-horizon）からしか標本を取らない。
デモがグリッパを開いてすぐ終わると、「開く」は行動の列の後ろにしか現れず、評価で実行する先頭の step では
開かないモデルになる（FANUC の DROID の並び、horizon 40 で実際に起きた: 掴んで箱の上まで運び、離さない）。
最後のフレーム（開いたまま止まる）を horizon - 1 回足すと、最後の step まで標本になる。
"""

from __future__ import annotations

import numpy as np


def hold_tail(n: int, horizon: int) -> np.ndarray:
    """0..n-1 の後に n-1 を horizon - 1 回繰り返す添字（エピソードの末尾に最後のフレームを足す）。"""
    return np.concatenate([np.arange(n), np.full(max(horizon - 1, 0), n - 1)]).astype(np.int64)


def last_change(values: np.ndarray, threshold: float = 0.5) -> int | None:
    """グリッパの行動（開閉の値）が最後に切り替わった step。切り替わりがなければ None。"""
    v = np.asarray(values, dtype=np.float64).reshape(-1) >= threshold
    idx = np.nonzero(v[1:] != v[:-1])[0]
    return int(idx[-1] + 1) if len(idx) else None


EXECUTION_HORIZON = 16  # evaluate_vla が 1 回の推論で実行する step 数の既定


def gripper_timing(grippers: list[np.ndarray], horizon: int, padded: bool, execution_horizon: int = EXECUTION_HORIZON) -> dict:
    """エピソードごとのグリッパの最後の切り替わりが、学習の標本（始まりは 0..T'-horizon、T' は末尾を足した長さ）の
    行動の列の先頭 execution_horizon step（評価で実際に実行する部分）に入るか。

    ok: すべてのエピソードで入る（開閉を、実行される行動として学べる）。"""
    after, without, unreachable = [], [], []
    for ep, g in enumerate(grippers):
        n = len(g)
        c = last_change(g)
        if c is None:
            without.append(ep)
            continue
        after.append(n - 1 - c)
        last_start = (n + (horizon - 1 if padded else 0)) - horizon
        if c - last_start >= execution_horizon:  # 最も後ろの標本でも、切り替わりが実行される部分より後ろ
            unreachable.append(ep)
    return {
        "ok": not unreachable,
        "horizon": horizon,
        "execution_horizon": execution_horizon,
        "padded_frames": horizon - 1 if padded else 0,
        "min_steps_after_last_change": min(after) if after else None,
        "episodes_without_gripper_change": without,
        "episodes_change_not_learnable": unreachable,
    }
