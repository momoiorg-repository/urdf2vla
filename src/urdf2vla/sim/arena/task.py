"""urdf2vla の Pick & Place を Arena の Task にする。

成功判定（物体が置き場所の半径内・台の上・グリッパを開いた状態が N step 続く）は記録・評価のループ側で行う
（urdf2vla.sim.state.SimView.task_success）。Arena の成功条件は満たした瞬間に episode を終えて reset するので、
「N step 保持」の判定やデモの記録と相性が悪い。ここでは時間切れだけを終了条件にする（Isaac Lab 2.x 版と同じ）。
"""

from __future__ import annotations


def make_task(c):
    import isaaclab.envs.mdp as mdp
    from isaaclab.envs.common import ViewerCfg
    from isaaclab.managers import EventTermCfg
    from isaaclab_arena.tasks.task_base import TaskBase
    from isaaclab_arena.tasks.task_termination_cfg import TaskTerminationCfg
    from isaaclab_arena.utils.configclass import make_configclass

    task = c.task
    # reset ではすべて（物体の候補も）を初期状態（関節目標も）に戻す。物体の候補は台から離れた所に置いてあり、
    # エピソードの始めに SimView.start_episode が 1 つを選んで台の上に置く
    events = [
        ("reset_scene", EventTermCfg, EventTermCfg(func=mdp.reset_scene_to_default, mode="reset", params={"reset_joint_targets": True})),
    ]
    EventsCfg = make_configclass("Urdf2VlaEventsCfg", events)
    front = next((cam for cam in c.cameras.cameras if cam.mount == "world"), None)
    eye = tuple(front.pos) if front else (task.table.center[0] + 1.2, 0.0, 0.8)
    lookat = (task.table.center[0], task.table.center[1], task.table.top_z)

    class Urdf2VlaPickPlaceTask(TaskBase):
        def __init__(self):
            super().__init__(episode_length_s=task.episode_length_s + 10.0, task_description=task.language_instruction)

        def get_scene_cfg(self):
            return None

        def get_termination_cfg(self) -> TaskTerminationCfg:
            # 記録・評価のループが episode_length_s で止める。環境側の時間切れはそれより後にして、最後の step で
            # 自動の reset が入らないようにする（入ると物体が置き場に戻り、最後の状態を記録できない）
            return TaskTerminationCfg(timeout_s=task.episode_length_s + 10.0)

        def get_events_cfg(self):
            return EventsCfg()

        def get_mimic_env_cfg(self, arm_mode):
            raise NotImplementedError("urdf2vla の Pick & Place は Isaac Lab Mimic に対応していません（scripted で収集します）")

        def get_metrics(self):
            return []

        def get_viewer_cfg(self) -> ViewerCfg:
            return ViewerCfg(eye=eye, lookat=lookat)

    return Urdf2VlaPickPlaceTask()
