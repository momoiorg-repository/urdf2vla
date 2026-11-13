"""evaluate_vla の失敗したエピソードに、何が起きたかの名前を付ける（純 Python）。

成功率だけでは直し方が分からない。例えば「掴んで持ち上げたが離さない」なら、学習データでグリッパを開く場面が
足りない（デモが開いた直後に終わる・行動の長さに対して末尾が短い）ことが多い。名前ごとに見るところを示す。
"""

from __future__ import annotations

LIFT_M = 0.03  # これより持ち上がっていなければ「掴めていない」

HINTS = {
    "never_closed": "グリッパを一度も閉じていません。物体に近づけていないか、閉じる行動を学べていません。評価の動画で手先が物体へ向かうかを見てください。",
    "missed_grasp": "グリッパを閉じましたが物体が持ち上がっていません。掴む位置・高さがずれています。デモ数を増やすか、物体の初期位置のばらつきを見直してください。",
    "never_released": "掴んで持ち上げましたが、最後までグリッパを開いていません。学習データでグリッパを開く場面が行動の列の先頭に来ていない可能性があります"
    "（convert_dataset の gripper_timing の検査、train_vla の release_check を確かめてください）。",
    "released_off_target": "持ち上げて離しましたが、置き場所から外れました。運ぶ先の位置を学べていないか、離すのが早すぎます。",
}


def failure_reason(outcome: str, closed_once: bool, gripper_open_at_end: bool, max_lift_m: float) -> str | None:
    """成功なら None。失敗なら HINTS のキー。"""
    if outcome == "success":
        return None
    if not closed_once:
        return "never_closed"
    if max_lift_m < LIFT_M:
        return "missed_grasp"
    if not gripper_open_at_end:
        return "never_released"
    return "released_off_target"


def summarize(reasons: list[str | None]) -> dict:
    """失敗の名前ごとの数と、いちばん多いものの直し方。"""
    counts: dict[str, int] = {}
    for r in reasons:
        if r is not None:
            counts[r] = counts.get(r, 0) + 1
    out: dict = {"counts": counts}
    if counts:
        top = max(counts, key=counts.get)
        out["most_common"] = top
        out["hint"] = HINTS[top]
    return out
