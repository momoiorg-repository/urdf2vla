"""Isaac Lab Mimic の手先と行動の並び（torch に依存しない部分。ホストでテストできるように分けてある）。

teleop 環境の行動は [腕ごとの目標 TCP 姿勢 7 次元 ..., グリッパごとの ±1 ...]（Arena の GR1T2 の Mimic と同じ並び）。
Mimic の手先（eef）は、単腕は "robot"（Arena の単腕の subtask_configs のキー）、双腕は arm の名前。
"""

from __future__ import annotations

EEF = "robot"


def eef_names(robot_cfg) -> list[str]:
    return [EEF] if len(robot_cfg.arms) == 1 else [a.name for a in robot_cfg.arms]


def arm_of(robot_cfg, eef: str):
    return robot_cfg.arms[0] if eef == EEF else next(a for a in robot_cfg.arms if a.name == eef)


def gripper_column(robot_cfg, arm_name: str) -> int | None:
    """行動で、その腕のグリッパの ±1 が入る列（腕ごとの 7 次元の後に、グリッパの順に並ぶ）。グリッパがなければ None。"""
    g = robot_cfg.gripper_for(arm_name)
    return None if g is None else 7 * len(robot_cfg.arms) + robot_cfg.grippers.index(g)


def grasp_signal(robot_cfg, arm_name: str) -> str:
    """subtask の終わりの信号（物体を掴んだ）の名前: 単腕は "grasp"、双腕は "grasp_<arm>"。"""
    return "grasp" if len(robot_cfg.arms) == 1 else f"grasp_{arm_name}"


def pick_working_arm(grasped: dict[str, list[str]]) -> str:
    """双腕の元のデモ（デモ名 → 物体を掴んだ腕の名前のリスト）から、作業する腕を決める。
    どのデモも 1 本の同じ腕で掴んでいること（Mimic の設定は腕ごとに 1 つ）。"""
    arms = set()
    for name, hit in grasped.items():
        if len(hit) != 1:
            raise ValueError(f"元のデモ {name} で物体を掴んだ腕が分かりません（掴んだ腕: {hit or 'なし'}）。1 本の腕で掴んで置いたデモを使ってください。")
        arms.add(hit[0])
    if not arms:
        raise ValueError("元のデモがありません。")
    if len(arms) != 1:
        raise ValueError(f"元のデモで掴んだ腕がそろっていません（{sorted(arms)}）。Mimic は同じ腕で掴んだデモから作ります。腕ごとに分けて記録してください。")
    return arms.pop()
