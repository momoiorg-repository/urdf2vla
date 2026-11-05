"""GR00T コンテナ内の Skill 実行エントリ: `python.sh -m urdf2vla.gr00t.entry <skill> --project <dir>`。"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

GR00T_SKILLS = {
    "convert_dataset": "urdf2vla.gr00t.convert",
    "train_vla": "urdf2vla.gr00t.train",
}


def main() -> None:
    from importlib import import_module

    from urdf2vla import skills as _skills  # noqa: F401
    from urdf2vla.project import Project
    from urdf2vla.registry import SKILLS
    from urdf2vla.result import SkillResult

    skill = sys.argv[1] if len(sys.argv) > 1 else ""
    p = argparse.ArgumentParser()
    p.add_argument("skill", choices=sorted(GR00T_SKILLS))
    p.add_argument("--project", required=True)
    if skill in SKILLS:
        for a in SKILLS[skill].args:
            p.add_argument(*a.flags, **a.kwargs)
    args = p.parse_args()

    project = Project(args.project)
    result = SkillResult(skill=args.skill)
    try:
        import_module(GR00T_SKILLS[args.skill]).run(project, args, result)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        result.fail(f"{type(e).__name__}: {e}")
    project.record(result)
    print(f"[urdf2vla] {args.skill}: {result.status}", flush=True)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
