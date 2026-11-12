"""urdf2vla CLI。各 Skill を単体で実行でき、結果は JSON 1 つを stdout に出す。

  urdf2vla init projects/franka --urdf path/to/robot.urdf
  urdf2vla inspect_robot --project projects/franka
  urdf2vla answer --project projects/franka arms.0.ee_link=panda_hand
  urdf2vla status --project projects/franka
  urdf2vla skills --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path

import yaml

from urdf2vla import skills as _skills  # noqa: F401  (Skill を登録する)
from urdf2vla.diagnose import diagnose_text, tail
from urdf2vla.project import Project, ProjectError, file_hash, open_logger
from urdf2vla.registry import OPTIONAL, PIPELINE, SKILLS
from urdf2vla.result import SkillResult
from urdf2vla.runtime.config import load_runtime
from urdf2vla.runtime.dispatch import run_sim_skill, stream_hint
from urdf2vla.urdf.parser import UrdfError

PROJECT_TEMPLATE = """\
# urdf2vla プロジェクト設定（このファイルはユーザが編集します）
name: {name}

robot:
  urdf: {urdf}
  # 以下は任意。書いておくと質問されません。
  # end_effector: panda_hand            # 双腕なら [left_ee, right_ee]
  # control_joints: [joint1, ...]       # arm として制御する関節（双腕なら {{left: [...], right: [...]}}）
  # gripper_joints: [finger1, finger2]  # 指の関節（自動検出を上書き）
  # gripper_open_position: upper        # 指が開くのは関節の lower / upper どちらの限界か
  # gripper_open_close: [[開, 閉], ...]  # 可動範囲のない連続回転の指だけ。グリッパの順に [rad]
  # front_axis: +x                      # 正面が +x でないロボット（+y / -x / -y）
  # default_pose: {{joint1: 0.0}}         # 初期姿勢 [rad or m]
  # effort_limits: {{"joint[0-6]_.*": 10.0}}  # トルク上限 [N·m]（関節名の正規表現。URDF の値が仮値のとき）
  # base_pose: {{pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1]}}   # 四元数は [x, y, z, w]
  # package_paths: [../my_ws/src]       # package:// を探す追加ディレクトリ
  # gripper: {{simready: robotiq_2f_85}}  # NVIDIA のアセットのグリッパを付ける（腕だけの URDF）・付け替える
  #                                     # （robotiq_2f_85 / ufactory_xarm。取付先は自動、mount_link: tool0 などで変更）

# Pick & Place の物体と容器。既定はりんご・ライム・洋梨・色付きの立方体（エピソードごとに 1 つ）と KLT の箱
# task:
#   objects: [apple, lime, pear, color_cube]   # ほかに rubiks_cube、プリミティブの cube（ダウンロード不要）
#   container: klt_bin                          # トレイなら plastic_tray、平らな緑の円なら disc

# カメラ。既定は DROID と同じ配置: 手先(wrist) + ロボットの左右の高い位置から台を見下ろす外部カメラ(left / right)。
# 双腕では wrist が腕ごとに展開され、手先 2 台 + 外部 2 台になる。
# ほかに正面(front)・横(side)のプリセットと、明示指定ができる。明示指定の例:
#   - {{name: head_cam, mount: link, parent_link: head_link, pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1],
#      convention: ros, width: 640, height: 480, intrinsic_matrix: [fx, 0, cx, 0, fy, cy, 0, 0, 1]}}
cameras:
  - preset: wrist
  - preset: left
  - preset: right

# 自動生成された設定の上書き（generated/ の各ファイルと同じ構造で書く）
overrides:
  robot: {{}}
  task: {{}}
  cameras: {{}}
"""


def _emit(result: SkillResult) -> int:
    print(result.to_json())
    return {"ok": 0, "needs_input": 2, "error": 1}[result.status]


def _project(args) -> Project:
    root = args.project or os.environ.get("URDF2VLA_PROJECT") or "."
    return Project(root)


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    result = SkillResult(skill="init")
    if (root / "project.yaml").exists() and not args.force:
        result.fail(f"{root}/project.yaml は既にあります（上書きするなら --force）")
        return _emit(result)
    template = None
    if args.from_:
        src = Path(args.from_).resolve()
        template = src.read_text()
        raw_urdf = (yaml.safe_load(template).get("robot") or {}).get("urdf")
        if not raw_urdf:
            result.fail(f"{src} に robot.urdf がありません")
            return _emit(result)
        urdf = (src.parent / Path(raw_urdf).expanduser()).resolve()
    elif args.urdf:
        urdf = Path(args.urdf).expanduser().resolve()
    else:
        result.fail("--urdf か --from を指定してください")
        return _emit(result)
    if not urdf.exists():
        result.fail(f"URDF が見つかりません: {urdf}")
        return _emit(result)
    root.mkdir(parents=True, exist_ok=True)
    rel = os.path.relpath(urdf, root)
    if rel.count("..") > 3:  # 遠い場所なら絶対パスの方が読みやすい
        rel = str(urdf)
    if template is not None:
        # コメントを残したまま robot.urdf だけ新しい場所からの相対パスに書き換える
        text = re.sub(r"(?m)^(\s*urdf:\s*).*$", lambda m: m.group(1) + rel, template, count=1)
        if args.name:
            text = re.sub(r"(?m)^name:.*$", f"name: {args.name}", text, count=1)
    else:
        text = PROJECT_TEMPLATE.format(name=args.name or root.name, urdf=rel)
    (root / "project.yaml").write_text(text)
    result.outputs["project"] = str(root / "project.yaml")
    result.next = ["inspect_robot"]
    return _emit(result)


def cmd_skills(args) -> int:
    items = [SKILLS[n].describe() for n in [*PIPELINE, *OPTIONAL] if n in SKILLS]
    if args.json:
        print(json.dumps({"pipeline": [i["name"] for i in items], "skills": items}, ensure_ascii=False, indent=2))
    else:
        for i in items:
            print(f"[phase {i['phase']}] {i['name']:<24} ({i['runs_in']}) {i['summary']}")
    return 0


def next_skills(project: Project) -> list[str]:
    state = project.state()
    out = []
    for name in PIPELINE:
        if name not in SKILLS:
            break
        status = state.get(name, {}).get("status")
        if not SKILLS[name].satisfied(status):
            out.append(name)
            break
    return out


def cmd_status(args) -> int:
    project = _project(args)
    state = project.state()
    rows = []
    for name in PIPELINE:
        if name not in SKILLS:
            continue
        s = state.get(name, {})
        rows.append({"skill": name, "phase": SKILLS[name].phase, "status": s.get("status", "pending"), "finished_at": s.get("finished_at")})
    print(json.dumps({"project": str(project.root), "steps": rows, "next": next_skills(project)}, ensure_ascii=False, indent=2))
    return 0


def cmd_show(args) -> int:
    project = _project(args)
    name = args.file if args.file.endswith(".yaml") else f"{args.file}.yaml"
    data = project.read_generated(name)
    print(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return 0


def _parse_value(raw: str):
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def cmd_answer(args) -> int:
    project = _project(args)
    result = SkillResult(skill="answer")
    values = {}
    for item in args.items:
        if "=" not in item:
            result.fail(f"key=value の形で指定してください: {item}")
            return _emit(result)
        k, v = item.split("=", 1)
        values[k.strip()] = _parse_value(v.strip())
    project.set_answers(values)
    result.outputs["answers"] = project.rel(project.root / "answers.yaml")
    result.data = {"recorded": values}
    result.next = next_skills(project) or ["configure_robot"]
    return _emit(result)


def cmd_diagnose(args) -> int:
    project = _project(args)
    result = SkillResult(skill="diagnose")
    target = args.skill
    latest = project.logs / f"latest_{target}.json" if target else None
    if target is None:
        failed = [n for n, s in project.state().items() if s.get("status") == "error"]
        target = failed[-1] if failed else None
        latest = project.logs / f"latest_{target}.json" if target else None
    if not latest or not latest.exists():
        result.data = {"findings": [], "note": "エラーになった Skill はありません"}
        return _emit(result)
    last = SkillResult.model_validate(json.loads(latest.read_text()))
    text = "\n".join(last.errors + last.warnings)
    log_tail = ""
    if last.log and (project.root / last.log).exists():
        log_tail = tail(project.root / last.log, 200)
        text += "\n" + log_tail
    findings = diagnose_text(text)
    result.data = {"skill": target, "errors": last.errors, "findings": findings, "log_tail": log_tail[-3000:]}
    return _emit(result)


def cmd_teleop_keys(args) -> int:
    """端末のキー入力を collect_dataset --device socket に送る（GUI 不要の teleop）。"""
    import socket
    import termios
    import tty

    try:
        sock = socket.create_connection(("127.0.0.1", args.port), timeout=5)
    except OSError:
        print(f"127.0.0.1:{args.port} に接続できません。先に `urdf2vla collect_dataset --device socket` を起動してください。", file=sys.stderr)
        return 1
    print("W/S A/D Q/E: 移動  Z/X T/G C/V: 回転  K: グリッパ  N: arm 切替  R: やり直し  ESC: 終了（Ctrl-C でこの画面だけ終了）")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x03":
                break
            key = "ESC" if ch == "\x1b" else ch.upper()
            sock.sendall((key + "\n").encode())
            if key == "ESC":
                break
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sock.close()
    return 0


def run_skill(name: str, args) -> int:
    spec = SKILLS[name]
    project = _project(args)
    log_path = project.new_log(name)
    opts = {a.flags[0].lstrip("-").replace("-", "_"): getattr(args, a.flags[0].lstrip("-").replace("-", "_"), None) for a in spec.args}
    state = project.state()
    missing = [r for r in spec.requires if not SKILLS[r].satisfied(state.get(r, {}).get("status"))]
    if missing:
        result = SkillResult(skill=name)
        result.fail(f"先に {', '.join(missing)} を成功させてください。")
        result.next = missing[:1]
        return _emit(result)

    if spec.runs_in == "host":
        result = SkillResult(skill=name, log=project.rel(log_path))
        log = open_logger(log_path, name)
        try:
            spec.func(project, opts, result, log)
        except (UrdfError, ProjectError, ValueError) as e:
            result.fail(str(e))
            log.error("%s", e)
        except Exception as e:  # noqa: BLE001
            result.fail(f"{type(e).__name__}: {e}")
            log.error("%s", traceback.format_exc())
    else:
        extra = []
        for a in spec.args:
            key = a.flags[0].lstrip("-").replace("-", "_")
            val = getattr(args, key, None)
            if key == "timeout" or val in (None, False):
                continue
            extra += [a.flags[0]] if val is True else [a.flags[0], str(val)]
        stream_hint(spec.runs_in)
        timeout = None if getattr(args, "livestream", False) else getattr(args, "timeout", None)
        result = run_sim_skill(load_runtime(spec.runs_in), name, project, extra, log_path, timeout, kind=spec.runs_in)
    if result.status == "error" and not result.next:
        result.next = ["diagnose"]
    project.record(result, inputs_hash=file_hash(project.root / "project.yaml", project.root / "answers.yaml"))
    return _emit(result)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="urdf2vla", description="URDF から Isaac Sim 上の VLA 開発環境を作る")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="プロジェクトを作る")
    s.add_argument("dir")
    s.add_argument("--urdf", help="ロボットの URDF")
    s.add_argument("--from", dest="from_", help="例の project.yaml から作る（examples/*/project.yaml）")
    s.add_argument("--name")
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("skills", help="Skill 一覧")
    s.add_argument("--json", action="store_true")

    for name, helptext in (("status", "工程の状態と次の Skill"), ("answer", "質問に回答する"), ("show", "生成された設定を表示"), ("diagnose", "失敗の原因と対処")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--project")
        if name == "answer":
            s.add_argument("items", nargs="+", help="key=value")
        if name == "show":
            s.add_argument("file", help="robot_metadata / robot_config / task / cameras ...")
        if name == "diagnose":
            s.add_argument("--skill")

    s = sub.add_parser("doctor", help="実行環境を検査する（GPU ドライバ、Docker、image・コンテナ、ディスク、アセットサーバ）。最初と、環境がおかしいときに")
    s.add_argument("--offline", action="store_true", help="アセットサーバに届くかは調べない")

    s = sub.add_parser("view", help="ブラウザでロボット・把持点・台・カメラを 3D で確かめ、質問に答える（pip install -e '.[view]' が必要）")
    s.add_argument("--project")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)

    s = sub.add_parser("teleop-keys", help="端末のキー入力で teleop する（collect_dataset --device socket と一緒に使う）")
    s.add_argument("--port", type=int, default=50555)

    for name in [*PIPELINE, *OPTIONAL]:
        if name not in SKILLS:
            continue
        spec = SKILLS[name]
        s = sub.add_parser(name, help=spec.summary)
        s.add_argument("--project")
        for a in spec.args:
            s.add_argument(*a.flags, **a.kwargs)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "skills":
            return cmd_skills(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "show":
            return cmd_show(args)
        if args.cmd == "answer":
            return cmd_answer(args)
        if args.cmd == "diagnose":
            return cmd_diagnose(args)
        if args.cmd == "teleop-keys":
            return cmd_teleop_keys(args)
        if args.cmd == "doctor":
            from urdf2vla.doctor import default_runtimes, run_doctor
            from urdf2vla.runtime.config import URDF2VLA_ROOT

            return _emit(run_doctor(URDF2VLA_ROOT, default_runtimes(), assets=not args.offline))
        if args.cmd == "view":
            try:
                import viser  # noqa: F401
                import yourdfpy  # noqa: F401
            except ImportError as e:
                print(f"[urdf2vla] view には追加のパッケージが必要です（{e.name} がありません）: uv pip install -e '.[view]'", file=sys.stderr)
                return 1
            from urdf2vla.view import run_viewer

            run_viewer(_project(args), host=args.host, port=args.port)
            return 0
        return run_skill(args.cmd, args)
    except ProjectError as e:
        r = SkillResult(skill=args.cmd)
        r.fail(str(e))
        return _emit(r)


if __name__ == "__main__":
    sys.exit(main())
