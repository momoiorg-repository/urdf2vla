"""Isaac Sim を使う Skill をランタイム（コンテナ or ローカル）で実行する。

ホスト CLI → docker exec → `python.sh -m urdf2vla.sim.entry <skill>` → 結果を logs/latest_<skill>.json に書く。
Kit の大量の標準出力はログファイルに流し、結果はファイルから読む。
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

from urdf2vla.project import Project
from urdf2vla.result import SkillResult
from urdf2vla.runtime.config import URDF2VLA_ROOT, Runtime


def container_running(runtime: Runtime) -> bool:
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", runtime.container], capture_output=True, text=True
    )
    return out.returncode == 0 and out.stdout.strip() == "true"


ENTRY = {"sim": "urdf2vla.sim.entry", "gr00t": "urdf2vla.gr00t.entry"}


def runtime_script(runtime: Runtime, command: str) -> str:
    """ランタイム内で実行するシェル（DISPLAY を外す・urdf2vla を import できるようにする・setup を実行）。"""
    src = runtime.to_runtime(URDF2VLA_ROOT / "src")
    paths = shlex.quote(src) + (f":{shlex.quote(runtime.gr00t_root)}" if runtime.gr00t_root else "")
    # DISPLAY が残っていると Kit の起動が数分止まる（X サーバ探索）。常に外す
    pre = f"unset DISPLAY; export PYTHONPATH={paths}:${{PYTHONPATH:-}}; export PYTHONDONTWRITEBYTECODE=1; "
    if runtime.gr00t_root:
        pre += f"export URDF2VLA_GR00T_ROOT={shlex.quote(runtime.gr00t_root)}; "
    if runtime.setup:
        pre += runtime.setup + "; "
    return pre + command


def build_command(runtime: Runtime, skill: str, project: Project, extra: list[str], kind: str = "sim") -> list[str]:
    proj = runtime.to_runtime(project.root)
    py = f"{runtime.python} -m {ENTRY[kind]} {shlex.quote(skill)} --project {shlex.quote(proj)} " + " ".join(
        shlex.quote(a) for a in extra
    )
    return wrap(runtime, runtime_script(runtime, py))


def wrap(runtime: Runtime, script: str) -> list[str]:
    if runtime.mode == "local":
        return ["bash", "-c", script]
    cmd = ["docker", "exec"]
    if runtime.user:
        cmd += ["-u", runtime.user, "-e", f"HOME=/home/{runtime.user}"]
    for k, v in runtime.env.items():
        cmd += ["-e", f"{k}={v}"]
    return cmd + [runtime.container, "bash", "-c", script]


def run_sim_skill(
    runtime: Runtime, skill: str, project: Project, extra: list[str], log_path: Path, timeout: float | None, kind: str = "sim"
) -> SkillResult:
    if runtime.mode == "docker" and not container_running(runtime):
        r = SkillResult(skill=skill, status="error")
        hint = "bash docker/run.sh" + (" gr00t" if kind == "gr00t" else "")
        r.fail(f"コンテナ {runtime.container} が起動していません。`{hint}` を実行してください。")
        return r
    result_file = project.logs / f"latest_{skill}.json"
    started = time.time()
    if result_file.exists():
        result_file.unlink()
    cmd = build_command(runtime, skill, project, extra, kind)
    with open(log_path, "w", encoding="utf-8") as log:
        log.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_in_runtime(runtime, skill, project)
            proc.kill()
            rc = None
        except KeyboardInterrupt:
            # docker exec のクライアントを止めてもコンテナ内の Isaac Sim は残るので、明示的に止める
            _kill_in_runtime(runtime, skill, project)
            proc.kill()
            print("[urdf2vla] 停止しました", file=sys.stderr)
            rc = 130
    if result_file.exists() and result_file.stat().st_mtime >= started - 1:
        result = SkillResult.model_validate(json.loads(result_file.read_text()))
    else:
        result = SkillResult(skill=skill, status="error")
        if rc is None:
            result.fail(f"{skill} が {timeout:.0f} 秒以内に終わりませんでした。")
        else:
            result.fail(f"{skill} が結果を出さずに終了しました（exit {rc}）。")
    result.log = project.rel(log_path)
    return result


def _kill_in_runtime(runtime: Runtime, skill: str, project: Project) -> None:
    if runtime.mode != "docker":
        return
    # "[u]rdf2vla" は自分（この pkill を含む bash）のコマンド行にはマッチしない
    pattern = shlex.quote(f"[u]rdf2vla.(sim|gr00t).entry {skill} --project {runtime.to_runtime(project.root)}")
    # Kit は SIGTERM で止まらないことがあるので、少し待って SIGKILL
    script = f"pkill -TERM -f {pattern}; for i in 1 2 3 4 5; do pgrep -f {pattern} >/dev/null || exit 0; sleep 1; done; pkill -KILL -f {pattern}"
    subprocess.run(["docker", "exec", runtime.container, "bash", "-c", script], capture_output=True)


def stream_hint(kind: str = "sim") -> None:
    what = "Isaac Sim" if kind == "sim" else "GR00T"
    print(f"[urdf2vla] {what} のコンテナで実行しています（ログ: logs/）...", file=sys.stderr)
