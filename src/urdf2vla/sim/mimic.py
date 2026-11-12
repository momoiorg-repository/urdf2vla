"""Isaac Lab Mimic: 少数のデモ（元のデモ）から、物体の位置を変えたデモを自動で作る。

Mimic は元のデモを「物体に対する手先の動き」の区切り（subtask）に分け、新しいエピソードの物体の位置に合わせて
手先の軌道を写し、実行して成功したものを残す。ここでは Pick & Place を 2 つの subtask に分ける:
  1. grasp: 物体まで行って掴む（物体の座標で写す）
  2. place: 容器まで運んで離す（容器の座標で写す）

Mimic 用の環境（Configs.mimic）は、物体を 1 つだけ台の上に置き、reset で位置をばらつかせ、成功を終了条件に持つ。
手先の姿勢・物体の姿勢はすべて env の原点の座標（Isaac Lab Mimic の既定の get_object_poses と同じ）で扱い、
環境の行動（ロボットの base 座標の目標 TCP 姿勢 + グリッパ ±1）との変換は env 側で行う。

双腕: 手先（eef）は腕ごと（名前は arm の名前）。Arena の ArmMode.LEFT / RIGHT と同じ形で、作業する腕（元のデモで物体を
掴んだ腕）が grasp → place、もう一方の腕は 1 つの区間（物体の座標で写さず、元のデモのまま = 初期姿勢で止まっている）。
行動は [腕ごとの目標 TCP 姿勢 7 次元 ..., グリッパごとの ±1 ...]（Arena の GR1T2 の Mimic と同じ並び）。
"""

from __future__ import annotations

from collections.abc import Sequence

import torch

from urdf2vla.mimic_layout import EEF, arm_of, eef_names, grasp_signal, gripper_column

GRASP_DIST = 0.05  # 掴んだとみなす TCP と物体の距離 [m]


def _tcp_env(env, robot_cfg, env_ids=None, arm=None) -> tuple[torch.Tensor, torch.Tensor]:
    """TCP の位置と向き（env の原点の座標）。arm を省くと最初の腕。"""
    from isaaclab.utils.math import quat_apply, quat_mul

    from urdf2vla.sim.lab import T

    if env_ids is None:
        env_ids = slice(None)
    arm = arm or robot_cfg.arms[0]
    robot = env.scene["robot"]
    bi = robot.body_names.index(arm.ik_body)
    pos = T(robot.data.body_pos_w)[env_ids, bi]
    quat = T(robot.data.body_quat_w)[env_ids, bi]
    off_p = torch.tensor([arm.tcp_offset.pos], device=pos.device).expand(pos.shape[0], 3)
    off_q = torch.tensor([arm.tcp_offset.rot_xyzw], device=pos.device).expand(pos.shape[0], 4)
    tcp = pos + quat_apply(quat, off_p) - env.scene.env_origins[env_ids]
    return tcp, quat_mul(quat, off_q)


def _root(env, env_ids=None) -> tuple[torch.Tensor, torch.Tensor]:
    from urdf2vla.sim.lab import T

    if env_ids is None:
        env_ids = slice(None)
    robot = env.scene["robot"]
    return T(robot.data.root_pos_w)[env_ids] - env.scene.env_origins[env_ids], T(robot.data.root_quat_w)[env_ids]


def _object_pos(env, name: str) -> torch.Tensor:
    from urdf2vla.sim.lab import T

    return T(env.scene[name].data.root_pos_w) - env.scene.env_origins


# ---------------------------------------------------------------------------- predicates（Arena の CompletionCriteria）


def object_in_container(env, object_name: str, target_name: str, half_xy: tuple, max_z: float, grip_cols: tuple = (-1,)) -> torch.Tensor:
    """物体が容器の縁より内側にあり、容器の底近くにあり、すべてのグリッパに開く指令が出ている（env ごとの bool）。
    双腕では作業しない腕のグリッパは開いたままなので、「すべて開く」= 作業した腕が物体を離した。"""
    p = _object_pos(env, object_name)
    t = _object_pos(env, target_name)
    inside = (torch.abs(p[:, 0] - t[:, 0]) < half_xy[0]) & (torch.abs(p[:, 1] - t[:, 1]) < half_xy[1])
    low = p[:, 2] < max_z
    released = (env.action_manager.action[:, list(grip_cols)] >= 0).all(dim=1)
    return inside & low & released


def success_predicate(task, robot_cfg=None):
    """Mimic の成功判定（Arena の成功条件）の 1 step 分: object_in_container を task の寸法とグリッパの列で固定したもの。"""
    from functools import partial

    t, o, sc = task.target, task.objects[0], task.success
    half = (t.size[0] / 2 - sc.container_margin, t.size[1] / 2 - sc.container_margin)
    max_z = task.table.top_z + min(t.size[2], 0.03) + o.size[2] + sc.max_height_above_table  # 剛体の原点の場所の差を見込んで物体の高さまで
    cols = (-1,)
    if robot_cfg is not None and robot_cfg.grippers:
        cols = tuple(7 * len(robot_cfg.arms) + i for i in range(len(robot_cfg.grippers)))
    return partial(object_in_container, object_name="object_0", target_name="target", half_xy=half, max_z=max_z, grip_cols=cols)


def object_grasped(env, robot_cfg, object_name: str, arm=None) -> torch.Tensor:
    """その腕のグリッパに閉じる指令が出ていて、物体がその腕の TCP の近くにある（env ごとの bool）。指の関節角では判定しない
    （物体を挟んだ Franka の指は、閉じた位置より開いた位置に近い）。"""
    arm = arm or robot_cfg.arms[0]
    tcp, _ = _tcp_env(env, robot_cfg, arm=arm)
    near = torch.linalg.norm(_object_pos(env, object_name) - tcp, dim=-1) < GRASP_DIST
    col = gripper_column(robot_cfg, arm.name)
    closed = env.action_manager.action[:, -1 if col is None else col] < 0
    return near & closed


# ---------------------------------------------------------------------------- Mimic の環境と設定


def make_mimic_env_class(robot_cfg):
    """Arena の Embodiment.mimic_env に渡す環境クラス（ManagerBasedRLMimicEnv の API を urdf2vla のロボットで実装）。
    手先は腕ごと（eef_names）。行動は [腕ごとの目標 TCP 姿勢（base 座標）7 次元 ..., グリッパごとの ±1 ...]。"""
    import isaaclab.utils.math as PoseUtils
    from isaaclab_arena.environments.isaaclab_arena_manager_based_env import IsaacLabArenaManagerBasedRLMimicEnv

    names = eef_names(robot_cfg)
    arms = {e: arm_of(robot_cfg, e) for e in names}
    cols = {e: gripper_column(robot_cfg, arms[e].name) for e in names}
    n_arm = len(robot_cfg.arms)

    class Urdf2VlaMimicEnv(IsaacLabArenaManagerBasedRLMimicEnv):
        eef_names = names

        def get_robot_eef_pose(self, eef_name: str, env_ids: Sequence[int] | None = None) -> torch.Tensor:
            pos, quat = _tcp_env(self, robot_cfg, env_ids, arms[eef_name])
            return PoseUtils.make_pose(pos, PoseUtils.matrix_from_quat(quat))

        def target_eef_pose_to_action(self, target_eef_pose_dict: dict, gripper_action_dict: dict, action_noise_dict: dict | None = None, env_id: int = 0) -> torch.Tensor:
            rp, rq = _root(self, [env_id])
            r_root = PoseUtils.matrix_from_quat(rq)[0]
            parts = []
            for e in names:  # robot_cfg.arms の順
                pos, rot = PoseUtils.unmake_pose(target_eef_pose_dict[e])  # env の原点の座標
                pos_b = r_root.T @ (pos - rp[0])  # ロボットの base 座標（行動の座標）
                rot_b = r_root.T @ rot
                if action_noise_dict is not None:
                    pos_b = pos_b + action_noise_dict[e] * torch.randn_like(pos_b)
                parts += [pos_b, PoseUtils.quat_from_matrix(rot_b.unsqueeze(0))[0]]
            for g in robot_cfg.grippers:  # グリッパの順。その腕の eef の開閉
                e = EEF if n_arm == 1 else g.arm
                parts.append(gripper_action_dict[e].reshape(-1)[:1])
            return torch.cat(parts, dim=0)

        def action_to_target_eef_pose(self, action: torch.Tensor) -> dict[str, torch.Tensor]:
            rp, rq = _root(self)
            r_root = PoseUtils.matrix_from_quat(rq)
            out = {}
            for k, e in enumerate(names):
                a = action[:, 7 * k : 7 * k + 7]
                pos = torch.bmm(r_root, a[:, :3].unsqueeze(-1)).squeeze(-1) + rp
                rot = torch.bmm(r_root, PoseUtils.matrix_from_quat(a[:, 3:7]))
                out[e] = PoseUtils.make_pose(pos, rot).clone()
            return out

        def actions_to_gripper_actions(self, actions: torch.Tensor) -> dict[str, torch.Tensor]:
            # グリッパのない腕は「開」のまま（Mimic は eef ごとにグリッパの行動を要求する）
            return {e: (actions[..., c : c + 1] if c is not None else torch.ones_like(actions[..., :1])) for e, c in cols.items()}

        def get_subtask_term_signals(self, env_ids: Sequence[int] | None = None) -> dict[str, torch.Tensor]:
            out = {}
            for e in names:
                g = object_grasped(self, robot_cfg, "object_0", arms[e])
                out[grasp_signal(robot_cfg, arms[e].name)] = g if env_ids is None else g[env_ids]
            return out

    return Urdf2VlaMimicEnv


def make_mimic_env_cfg(task, generation_num_trials: int = 100, robot_cfg=None, working_arm: str | None = None):
    """Mimic の設定: grasp（物体の座標で写す）→ place（容器の座標で写す）。generation_num_trials 回試す（成功するまで続けない）。
    双腕では working_arm（元のデモで物体を掴んだ腕）が grasp → place、ほかの腕は 1 区間（座標を写さず元のデモのまま）。"""
    from isaaclab.envs.mimic_env_cfg import MimicEnvCfg, SubTaskConfig
    from isaaclab.utils import configclass
    from isaaclab_arena.tasks.common.mimic_default_params import MIMIC_DATAGEN_CONFIG_DEFAULTS

    @configclass
    class Urdf2VlaMimicEnvCfg(MimicEnvCfg):
        def __post_init__(self):
            super().__post_init__()
            self.datagen_config.name = "urdf2vla_pick_place"
            for key, value in MIMIC_DATAGEN_CONFIG_DEFAULTS.items():
                setattr(self.datagen_config, key, value)
            self.datagen_config.generation_num_trials = generation_num_trials
            self.datagen_config.generation_guarantee = False  # 試行回数で止める（成功数を保証するまで続けない）
            common = dict(selection_strategy="nearest_neighbor_object", selection_strategy_kwargs={"nn_k": 3},
                          action_noise=0.003, num_interpolation_steps=5, num_fixed_steps=0, apply_noise_during_interpolation=False)  # fmt: skip
            if robot_cfg is None or len(robot_cfg.arms) == 1:
                self.subtask_configs[EEF] = [
                    SubTaskConfig(object_ref="object_0", subtask_term_signal="grasp", subtask_term_offset_range=(0, 0), **common),
                    SubTaskConfig(object_ref="target", subtask_term_signal=None, subtask_term_offset_range=(0, 0), **common),
                ]
                return
            # 双腕: 作業する腕を先に（元のデモの選び方はこの腕の「物体に近いもの」で決まり、ほかの腕も同じデモを使う）。
            # 元のデモを記録する間はまだ分からない（記録では subtask の設定を使わず、腕ごとの信号をすべて記録する）
            names = [a.name for a in robot_cfg.arms]
            working = working_arm or names[0]
            assert working in names, working_arm
            self.subtask_configs[working] = [
                SubTaskConfig(object_ref="object_0", subtask_term_signal=grasp_signal(robot_cfg, working), subtask_term_offset_range=(0, 0), **common),
                SubTaskConfig(object_ref="target", subtask_term_signal=None, subtask_term_offset_range=(0, 0), **common),
            ]
            idle = dict(common, selection_strategy="random", selection_strategy_kwargs={}, action_noise=0.0)
            for arm in robot_cfg.arms:
                if arm.name != working:  # 物体の座標で写すと、物体を動かした分だけ止まっている手も動く（容器に当たる）
                    self.subtask_configs[arm.name] = [SubTaskConfig(object_ref=None, subtask_term_signal=None, subtask_term_offset_range=(0, 0), **idle)]

    return Urdf2VlaMimicEnvCfg()


def make_recorder_cfg(output_dir: str, file_name: str):
    """元のデモの記録: 行動・状態に加え、Mimic が使う datagen_info（手先・物体・目標の手先の姿勢、subtask の終わりの信号）。
    Isaac Lab Mimic の annotate_demos.py（自動の注釈）が記録するものと同じ。成功したエピソードだけを手で書き出す
    （record_demos.py と同じく reset では書き出さない）。"""
    from isaaclab.envs.mdp.recorders.recorders_cfg import ActionStateRecorderManagerCfg
    from isaaclab.managers.recorder_manager import DatasetExportMode, RecorderTerm, RecorderTermCfg
    from isaaclab.utils import configclass

    class DatagenInfoRecorder(RecorderTerm):
        def record_pre_step(self):
            env = self._env
            return "obs/datagen_info", {
                "object_pose": env.get_object_poses(),
                "eef_pose": {e: env.get_robot_eef_pose(eef_name=e) for e in env.eef_names},
                "target_eef_pose": env.action_to_target_eef_pose(env.action_manager.action),
            }

    class SubtaskTermRecorder(RecorderTerm):
        def record_pre_step(self):
            return "obs/datagen_info/subtask_term_signals", self._env.get_subtask_term_signals()

    @configclass
    class DatagenInfoRecorderCfg(RecorderTermCfg):
        class_type: type[RecorderTerm] = DatagenInfoRecorder

    @configclass
    class SubtaskTermRecorderCfg(RecorderTermCfg):
        class_type: type[RecorderTerm] = SubtaskTermRecorder

    @configclass
    class Urdf2VlaMimicRecorderCfg(ActionStateRecorderManagerCfg):
        record_pre_step_datagen_info = DatagenInfoRecorderCfg()
        record_pre_step_subtask_term_signals = SubtaskTermRecorderCfg()

    cfg = Urdf2VlaMimicRecorderCfg()
    cfg.dataset_export_dir_path = output_dir
    cfg.dataset_filename = file_name
    cfg.dataset_export_mode = DatasetExportMode.EXPORT_SUCCEEDED_ONLY
    cfg.export_in_record_pre_reset = False
    return cfg
