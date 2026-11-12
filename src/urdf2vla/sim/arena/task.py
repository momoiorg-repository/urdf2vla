"""urdf2vla の Pick & Place を Arena の Task にする。

成功判定（物体が置き場所の半径内・台の上・グリッパを開いた状態が N step 続く）は記録・評価のループ側で行う
（urdf2vla.sim.state.SimView.task_success）。Arena の成功条件は満たした瞬間に episode を終えて reset するので、
「N step 保持」の判定やデモの記録と相性が悪い。ここでは時間切れだけを終了条件にする（Isaac Lab 2.x 版と同じ）。
例外は Isaac Lab Mimic の環境（Configs.mimic）: 生成の成否を Arena の成功条件（_success_criteria）で決める。
"""

from __future__ import annotations


def _success_criteria(task, robot_cfg=None):
    """物体が容器の中に置かれ、グリッパに開く指令が出ている状態が 10 step 続く（SimView.task_success と同じ考え方）。"""
    from isaaclab_arena.progress_tracking.completion_criteria import CompletionCriteria
    from isaaclab_arena.tasks.predicates.temporal import TrueForConsecutiveStepsCfg

    from urdf2vla.sim.mimic import success_predicate

    pred = success_predicate(task, robot_cfg)
    return CompletionCriteria(name="object_in_container", predicate_sequence=[TrueForConsecutiveStepsCfg(predicate=pred, required_steps=10)])


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
    if c.mimic:  # Isaac Lab Mimic: reset のたびに物体を置く範囲の中でばらつかせる（Mimic のループが reset を行う）
        from isaaclab.managers import SceneEntityCfg

        o = task.objects[0]
        hx, hy = (task.spawn_x[1] - task.spawn_x[0]) / 2, (task.spawn_y[1] - task.spawn_y[0]) / 2
        events.append((
            "reset_object", EventTermCfg,
            EventTermCfg(func=mdp.reset_root_state_uniform, mode="reset",
                         params={"pose_range": {"x": (-hx, hx), "y": (-hy, hy), "yaw": (-0.5, 0.5) if o.box else (0.0, 0.0)},
                                 "velocity_range": {}, "asset_cfg": SceneEntityCfg("object_0")}),
        ))  # fmt: skip
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
            if c.mimic:  # Mimic の生成は成功の終了条件で成否を決める
                return TaskTerminationCfg(timeout_s=task.episode_length_s + 10.0, success=[_success_criteria(task, c.robot)])
            return TaskTerminationCfg(timeout_s=task.episode_length_s + 10.0)

        def get_events_cfg(self):
            return EventsCfg()

        def get_mimic_env_cfg(self, arm_mode):
            from urdf2vla.sim.mimic import make_mimic_env_cfg

            return make_mimic_env_cfg(task, c.mimic_trials, c.robot, c.mimic_working_arm)

        def get_metrics(self):
            return []

        def get_viewer_cfg(self) -> ViewerCfg:
            return ViewerCfg(eye=eye, lookat=lookat)

    return Urdf2VlaPickPlaceTask()
