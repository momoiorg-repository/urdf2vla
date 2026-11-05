"""Isaac Sim 内の Skill 実行エントリ: `python.sh -m urdf2vla.sim.entry <skill> --project <dir>`。

結果は <project>/logs/latest_<skill>.json に書く（ホスト CLI がそれを読む）。
Isaac Sim は終了処理で止まることがあるため、結果を書いたら os._exit で抜ける。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

_SIM_CONTEXT = None  # Isaac Sim（Kit）の起動コンテキスト。プロセスの最後まで保持する

SIM_SKILLS = {
    "import_robot": "urdf2vla.sim.import_robot",
    "launch_sim": "urdf2vla.sim.launch",
    "setup_teleoperation": "urdf2vla.sim.teleop_check",
    "collect_dataset": "urdf2vla.sim.collect",
    "evaluate_vla": "urdf2vla.sim.evaluate",
}


def main() -> None:
    from urdf2vla import skills as _skills  # noqa: F401  (registry に Skill の引数定義を載せる)
    from urdf2vla.registry import SKILLS

    skill = sys.argv[1] if len(sys.argv) > 1 else ""
    p = argparse.ArgumentParser()
    p.add_argument("skill", choices=sorted(SIM_SKILLS))
    p.add_argument("--project", required=True)
    if skill in SKILLS:
        for a in SKILLS[skill].args:
            p.add_argument(*a.flags, **a.kwargs)
    args = p.parse_args()

    from isaaclab.app import launch_simulation

    # Isaac Lab 3.0: Kit を headless で起動し、カメラの描画を有効にする（無いと画像が黒い）
    launcher = {"headless": True, "enable_cameras": True, "require_kit": True}
    if getattr(args, "livestream", False):
        launcher["livestream"] = 2
    # 終了処理（コンテキストを抜ける）は行わない: Isaac Sim は終了処理で止まることがあるので、結果を書いたら os._exit で抜ける。
    # コンテキストへの参照を持ち続けること（参照が切れると generator が閉じられ、その場で Kit が終了する）
    global _SIM_CONTEXT
    _SIM_CONTEXT = launch_simulation(None, launcher)
    _SIM_CONTEXT.__enter__()
    import omni.kit.app

    app = omni.kit.app.get_app()

    from importlib import import_module

    from urdf2vla.project import Project
    from urdf2vla.result import SkillResult

    project = Project(args.project)
    result = SkillResult(skill=args.skill)
    try:
        import_module(SIM_SKILLS[args.skill]).run(project, args, result, app)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        result.fail(f"{type(e).__name__}: {e}")
    project.record(result)
    print(f"[urdf2vla] {args.skill}: {result.status}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
