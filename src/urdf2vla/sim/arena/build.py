"""Embodiment + Scene + Task → Isaac Lab Arena の ArenaEnvBuilder で Isaac Lab の環境を作る。"""

from __future__ import annotations

import re

WARMUP_RENDER_STEPS = 5  # 環境を作った直後に描画を立ち上げるための step 数


def env_name(robot_name: str, mode: str) -> str:
    # Isaac Lab 3.0 の慣習に合わせ、gym の版番号（-v0）は付けない
    return f"Urdf2Vla-{re.sub(r'[^A-Za-z0-9]', '', robot_name.title())}-PickPlace-{'Teleop' if mode == 'teleop' else 'Policy'}"


def make_env(c, mode: str = "policy"):
    """Configs（urdf2vla.sim.common）から環境を作って reset する。(env（unwrapped）, env の名前) を返す。"""
    from isaaclab_arena.environments.arena_env_builder import ArenaEnvBuilder
    from isaaclab_arena.environments.arena_env_builder_cfg import ArenaEnvBuilderCfg
    from isaaclab_arena.environments.isaaclab_arena_environment import IsaacLabArenaEnvironment

    from urdf2vla.sim.arena.embodiment import make_embodiment
    from urdf2vla.sim.arena.scene import make_scene
    from urdf2vla.sim.arena.task import make_task

    task = c.task

    def tune(env_cfg):
        # 制御周期と描画（記録した fps = task.control_hz と一致させる）
        env_cfg.decimation = task.decimation
        # 記録・評価のループが episode_length_s で止める。環境側の時間切れは 10 秒後にして、最後の step で自動の reset が入らないようにする
        env_cfg.episode_length_s = task.episode_length_s + 10.0
        env_cfg.sim.dt = task.sim_dt
        env_cfg.sim.render_interval = task.decimation
        # reset 直後の画像に前のエピソードの場面が残らないよう、reset で描画し直す（Isaac Lab の画像入力タスクと同じ）
        env_cfg.num_rerenders_on_reset = 3
        if c.mimic_record is not None:  # Mimic の元のデモ: datagen_info を含む Isaac Lab の HDF5 に記録する
            from urdf2vla.sim.mimic import make_recorder_cfg

            env_cfg.recorders = make_recorder_cfg(*c.mimic_record)
            env_cfg.terminations.success = None  # 成功は記録の工程が判定して書き出す（env が先に reset しないように）
        if c.mimic_generate is not None:  # Mimic の生成: 成功したエピソードを書き出す。成功の終了条件は生成ループが判定する
            from isaaclab.envs.mdp.recorders.recorders_cfg import ActionStateRecorderManagerCfg
            from isaaclab.managers.recorder_manager import DatasetExportMode

            rec = ActionStateRecorderManagerCfg()
            rec.dataset_export_dir_path, rec.dataset_filename = c.mimic_generate
            rec.dataset_export_mode = DatasetExportMode.EXPORT_SUCCEEDED_ONLY
            env_cfg.recorders = rec
            c.mimic_success_term = env_cfg.terminations.success
            env_cfg.terminations.success = None
        return env_cfg

    name = env_name(c.robot.robot_name, mode) + ("-Mimic" if c.mimic else "")
    arena_env = IsaacLabArenaEnvironment(
        name=name,
        scene=make_scene(c),
        embodiment=make_embodiment(c, mode),
        task=make_task(c),
        env_cfg_callback=tune,
    )
    cfg = ArenaEnvBuilderCfg(
        num_envs=1,
        env_spacing=3.0,
        solve_relations=False,  # 配置は urdf2vla が決める（configure_task）
        language_instruction=task.language_instruction,
        mimic=c.mimic,
    )
    env = ArenaEnvBuilder(arena_env, cfg).make_registered().unwrapped
    # 作った直後は描画が立ち上がっておらず、reset の再描画だけでは最初のエピソードの最初の画像が reset 前の場面
    # （初期関節角のロボット）のまま残る（記録すると最初のフレームだけ姿勢・物体の位置が違うデモになる）。
    # 物理と描画を数 step 進めてから reset し直す（2 回目以降のエピソードは step の後の reset なので問題ない）
    env.reset()
    for _ in range(WARMUP_RENDER_STEPS):
        env.sim.step(render=True)
        env.scene.update(dt=env.physics_dt)
    env.reset()
    return env, name
