"""Isaac Lab Mimic の 2 つの工程（Isaac Sim 内、generate_mimic が順に呼ぶ）。

  source:   スクリプトで元のデモを集め、Mimic が使う datagen_info を含む Isaac Lab の HDF5 に書く
            （datasets/<name>_mimic_src.hdf5。Isaac Lab Mimic の annotate_demos.py --auto と同じ情報）
  generate: 元のデモから Isaac Lab Mimic の生成を generation_num_trials 回試し、成功したエピソードを書く
            （datasets/<name>_mimic_gen.hdf5。行動と初期状態だけで画像はない。collect_dataset --device mimic で画像付きに記録し直す）
"""

from __future__ import annotations

import torch

from urdf2vla.project import Project
from urdf2vla.result import SkillResult


def _mimic_configs(project: Project, result: SkillResult):
    from urdf2vla.sim.common import load_configs

    c = load_configs(project)
    if len(c.task.objects) != 1 or not c.task.target.is_container:
        result.fail(
            "Mimic は物体 1 つと容器のタスクで使います。project.yaml の task.objects を 1 つ（例: [color_cube]）にし、"
            "configure_task からやり直してください。"
        )
        return None
    if c.vla is None or c.teleop is None:
        result.fail("vla_interface.yaml / teleop.yaml がありません。configure_vla_interface と setup_teleoperation を先に実行してください。")
        return None
    c.mimic = True
    return c


def _close_dataset(env) -> None:
    """記録中の HDF5 を閉じる（entry は os._exit で抜けるので、env.close の後始末が走らない）。"""
    handler = getattr(env.recorder_manager, "_dataset_file_handler", None)
    if handler is not None:
        handler.close()


def run_source(project: Project, args, result: SkillResult, app=None) -> None:
    from urdf2vla.sim.common import make_env
    from urdf2vla.sim.lab import render_fix
    from urdf2vla.sim.state import SimView
    from urdf2vla.sim.teleop import TargetIntegrator, make_device

    c = _mimic_configs(project, result)
    if c is None:
        return
    out = project.root / "datasets"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{args.dataset}_mimic_src.hdf5"
    (out / name).unlink(missing_ok=True)
    c.mimic_record = (str(out), name)
    env, env_id = make_env(c, "teleop")
    torch.manual_seed(int(args.seed))
    view = SimView(env, c.robot, c.task, c.vla, project_root=project.root)
    view.active = 0
    from urdf2vla.sim.mimic import success_predicate

    arena_success = success_predicate(c.task)  # 生成の成否を決める判定。元のデモもこれを満たしたものだけを使う
    device = make_device("scripted", c.teleop, view)
    integ = TargetIntegrator(c.teleop, view)
    saved = attempts = 0
    episodes = []
    while saved < int(args.source_demos) and attempts < 3 * int(args.source_demos):
        attempts += 1
        env.reset()  # 物体は Task の reset event がばらつかせる。記録の初期状態もこの時点
        render_fix(env, c.task)
        device.reset(view)
        integ.reset(view)
        held, ok = 0, False
        for _ in range(c.teleop.max_episode_steps):
            cmd = device.advance(view, integ)
            env.step(integ.action(cmd, view, c.teleop.scripted_max_step))
            render_fix(env, c.task)
            open_now = all(cmd.gripper_open.get(g.name, True) for g in c.robot.grippers)
            held = held + 1 if view.task_success(open_now) else 0
            # Mimic の成功判定（Arena の終了条件、連続 10 step）が生成した軌道の最後までに満たされるよう、余分に続ける
            if held >= c.teleop.success_hold_steps + 10:
                ok = True
                break
            if cmd.done:
                break
        arena_ok = bool(arena_success(env)[0])
        if ok and not arena_ok:
            ok = False  # SimView では成功だが Mimic の成功判定（容器の中・底近く）を満たさない: 元のデモに使わない
        episodes.append({"attempt": attempts, "success": ok, "arena_success": arena_ok})
        print(f"[urdf2vla] mimic source attempt {attempts}: {'success' if ok else 'failed'} (arena_success={arena_ok})", flush=True)
        if ok:  # Isaac Lab の record_demos.py と同じ手順で、このエピソードだけを書き出す
            env.recorder_manager.record_pre_reset([0], force_export_or_skip=False)
            env.recorder_manager.set_success_to_episodes([0], torch.tensor([[True]], device=env.device))
            env.recorder_manager.export_episodes([0])
            saved += 1
    _close_dataset(env)
    result.outputs["mimic_source"] = project.rel(out / name)
    result.data = {"env_id": env_id, "saved": saved, "attempts": attempts, "episodes": episodes}
    if saved == 0:
        result.fail("Mimic の元のデモを 1 つも集められませんでした（スクリプトの把持が失敗しています。collect_dataset で確かめてください）。")


def run_generate(project: Project, args, result: SkillResult, app=None) -> None:
    import asyncio
    import random

    import numpy as np

    from isaaclab_mimic.datagen import generation
    from isaaclab_mimic.datagen.generation import env_loop, setup_async_generation

    from urdf2vla.sim.common import make_env

    c = _mimic_configs(project, result)
    if c is None:
        return
    out = project.root / "datasets"
    src = out / f"{args.dataset}_mimic_src.hdf5"
    if not src.exists():
        result.fail(f"{project.rel(src)} がありません（generate_mimic の元のデモの工程が先です）。")
        return
    name = f"{args.dataset}_mimic_gen.hdf5"
    (out / name).unlink(missing_ok=True)
    c.mimic_trials = int(args.trials)
    c.mimic_generate = (str(out), name)
    env, env_id = make_env(c, "teleop")
    seed = int(args.seed)
    env.cfg.datagen_config.seed = seed
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    env.reset()
    comps = setup_async_generation(env=env, num_envs=1, input_file=str(src), success_term=c.mimic_success_term, pause_subtask=False)
    tasks = asyncio.ensure_future(asyncio.gather(*comps["tasks"]))
    try:
        env_loop(env, comps["reset_queue"], comps["action_queue"], comps["info_pool"], comps["event_loop"], data_gen_tasks=tasks)
    except asyncio.CancelledError:
        pass
    finally:
        tasks.cancel()
        try:
            comps["event_loop"].run_until_complete(tasks)
        except (asyncio.CancelledError, Exception):  # noqa: BLE001  生成の非同期タスクの後始末
            pass
    _close_dataset(env)
    n, attempts = generation.num_success, generation.num_attempts
    print(f"[urdf2vla] mimic generated {n}/{attempts} successful episodes", flush=True)
    result.outputs["mimic_generated"] = project.rel(out / name)
    result.data = {"env_id": env_id, "trials": int(args.trials), "attempts": attempts, "generated_successes": n,
                   "success_rate": round(n / attempts, 3) if attempts else 0.0}  # fmt: skip
    if n == 0:
        result.fail("Mimic の生成で成功したエピソードがありません。")
