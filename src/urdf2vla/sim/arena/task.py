"""urdf2vla の Pick & Place を Arena の Task にする。

成功判定（物体が置き場所の半径内・台の上・グリッパを開いた状態が N step 続く）は記録・評価のループ側で行う
（urdf2vla.sim.state.SimView.task_success）。Arena の成功条件は満たした瞬間に episode を終えて reset するので、
「N step 保持」の判定やデモの記録と相性が悪い。ここでは時間切れだけを終了条件にする（Isaac Lab 2.x 版と同じ）。
"""

from __future__ import annotations


def make_task(c):
    import isaaclab.envs.mdp as mdp
    from isaaclab.envs.common import ViewerCfg
    from isaaclab.managers import EventTermCfg, SceneEntityCfg
    from isaaclab_arena.tasks.task_base import TaskBase
    from isaaclab_arena.tasks.task_termination_cfg import TaskTerminationCfg
    from isaaclab_arena.utils.configclass import make_configclass

    task = c.task
    obj = task.object
    half_x = (obj.spawn_x[1] - obj.spawn_x[0]) / 2
    half_y = (obj.spawn_y[1] - obj.spawn_y[0]) / 2
    # reset の順序: すべてを初期状態（関節目標も）に戻してから、物体を台の上でばらつかせる
    events = [
        ("reset_scene", EventTermCfg, EventTermCfg(func=mdp.reset_scene_to_default, mode="reset", params={"reset_joint_targets": True})),
        (
            "reset_object",
            EventTermCfg,
            EventTermCfg(
                func=mdp.reset_root_state_uniform,
                mode="reset",
                params={
                    "pose_range": {"x": (-half_x, half_x), "y": (-half_y, half_y), "yaw": (-0.5, 0.5)},
                    "velocity_range": {},
                    "asset_cfg": SceneEntityCfg("object"),
                },
            ),
        ),
    ]
    EventsCfg = make_configclass("Urdf2VlaEventsCfg", events)
    front = next((cam for cam in c.cameras.cameras if cam.mount == "world"), None)
    eye = tuple(front.pos) if front else (task.table.center[0] + 1.2, 0.0, 0.8)
    lookat = (task.table.center[0], task.table.center[1], task.table.top_z)

    class Urdf2VlaPickPlaceTask(TaskBase):
        def __init__(self):
            super().__init__(episode_length_s=task.episode_length_s, task_description=task.language_instruction)

        def get_scene_cfg(self):
            return None

        def get_termination_cfg(self) -> TaskTerminationCfg:
            return TaskTerminationCfg(timeout_s=task.episode_length_s)

        def get_events_cfg(self):
            return EventsCfg()

        def get_mimic_env_cfg(self, arm_mode):
            raise NotImplementedError("urdf2vla の Pick & Place は Isaac Lab Mimic に対応していません（scripted で収集します）")

        def get_metrics(self):
            return []

        def get_viewer_cfg(self) -> ViewerCfg:
            return ViewerCfg(eye=eye, lookat=lookat)

    return Urdf2VlaPickPlaceTask()
