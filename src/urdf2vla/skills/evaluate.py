"""evaluate_vla（ホスト）: GR00T 推論サーバを urdf2vla-gr00t で起動し、Isaac Sim 側のクライアントで評価する。"""

from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path

from urdf2vla.project import Project
from urdf2vla.registry import Arg, skill
from urdf2vla.result import SkillResult
from urdf2vla.runtime.config import load_runtime
from urdf2vla.runtime.dispatch import container_running, run_sim_skill, runtime_script, wrap


def _server_args(project: Project, opts: dict, gr) -> list[str] | str:
    # modality config（NEW_EMBODIMENT の定義）は変換済みデータセットにある。モデルのモードでも先に読み込む
    ds = project.root / "datasets" / f"{opts['dataset']}_gr00t"
    # 旧名（easyVLA）で変換したデータは easyvla_modality_*.py を持つ
    mods = sorted([*ds.glob("urdf2vla_modality_*.py"), *ds.glob("easyvla_modality_*.py")]) if ds.exists() else []
    if not mods:
        return f"{project.rel(ds)} がありません。convert_dataset --dataset {opts['dataset']} を先に実行してください（modality 定義が必要）。"
    pre = ["--urdf2vla-modality", gr.to_runtime(mods[0])]
    if opts.get("replay"):
        return pre + [
            "--dataset-path", gr.to_runtime(ds),
            "--modality-config-path", gr.to_runtime(mods[0]),
            "--execution-horizon", str(opts["execution_horizon"]),
        ]  # fmt: skip
    model = opts.get("model")
    if not model:
        return "--model（学習済み checkpoint のディレクトリ）か --replay を指定してください。"
    path = Path(model).expanduser()
    path = path if path.is_absolute() else project.root / path
    if not path.exists():
        return f"checkpoint が見つかりません: {path}"
    return pre + ["--model-path", gr.to_runtime(path)]


@skill(
    "evaluate_vla",
    phase=4,
    runs_in="host",
    summary="GR00T 推論サーバを起動し、Isaac Sim 上で VLA を動かして成功率を測る（--replay で学習前に通信経路と行動の適用を検証）",
    inputs=["--model <checkpoint> | --replay datasets/<name>_gr00t/", "generated/vla_interface.yaml"],
    outputs=["build/eval_report.yaml", "snapshots/eval_*.png", "build/eval_videos/*.mp4 (--video)"],
    requires=["setup_teleoperation"],
    args=[
        Arg(("--model",), {"default": None, "help": "学習済み checkpoint のディレクトリ"}),
        Arg(("--replay",), {"action": "store_true", "help": "モデルの代わりに変換済みデータの行動を返すサーバで検証する"}),
        Arg(("--dataset",), {"default": "demos", "help": "--replay で使う datasets/<名前>"}),
        Arg(("--episodes",), {"type": int, "default": 10}),
        Arg(("--execution-horizon",), {"type": int, "default": 16, "help": "1 回の推論で実行する step 数"}),
        Arg(("--port",), {"type": int, "default": 5555}),
        Arg(("--seed",), {"type": int, "default": 0}),
        Arg(("--timeout",), {"type": float, "default": 3600.0, "help": "秒"}),
        Arg(("--video",), {"action": "store_true", "help": "エピソードごとに VLA が見る画像を横に並べた動画を build/eval_videos/ に保存する"}),
    ],
)
def evaluate_vla(project: Project, opts: dict, result: SkillResult, log) -> None:
    gr, sim = load_runtime("gr00t"), load_runtime("sim")
    for rt, hint in ((gr, "bash docker/run.sh gr00t"), (sim, "bash docker/run.sh")):
        if rt.mode == "docker" and not container_running(rt):
            result.fail(f"コンテナ {rt.container} が起動していません。`{hint}` を実行してください。")
            return
    sargs = _server_args(project, opts, gr)
    if isinstance(sargs, str):
        result.fail(sargs)
        return
    port = int(opts["port"])
    sargs += ["--embodiment-tag", "new_embodiment", "--port", str(port), "--host", "127.0.0.1"]
    server_log = project.new_log("gr00t_server")
    cmd = wrap(gr, runtime_script(gr, f"cd /tmp && PYTHONUNBUFFERED=1 {gr.python} -m urdf2vla.gr00t.server " + " ".join(shlex.quote(a) for a in sargs)))
    log.info("server: %s", " ".join(cmd))
    with open(server_log, "w") as f:
        server = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
    try:
        # 準備完了を待つ（モデルの読み込みに数分かかることがある）
        ready = False
        for _ in range(600):
            text = server_log.read_text(errors="replace")
            if "Server ready" in text:
                ready = True
                break
            if server.poll() is not None:
                break
            time.sleep(1)
        if not ready:
            result.fail(f"GR00T サーバが起動しませんでした。ログ: {project.rel(server_log)}")
            result.data = {"server_log_tail": server_log.read_text(errors="replace")[-2000:]}
            return
        extra = ["--port", str(port), "--episodes", str(opts["episodes"]), "--execution-horizon", str(opts["execution_horizon"]),
                 "--dataset", opts["dataset"], "--seed", str(opts["seed"])]  # fmt: skip
        if opts.get("replay"):
            extra.append("--replay")
        if opts.get("video"):
            extra.append("--video")
        if opts.get("model"):
            extra += ["--model", str(opts["model"])]
        sim_log = project.new_log("evaluate_vla_sim")
        r = run_sim_skill(sim, "evaluate_vla", project, extra, sim_log, opts.get("timeout"))
        for field in ("status", "outputs", "warnings", "errors", "data"):
            setattr(result, field, getattr(r, field))
        result.outputs["server_log"] = project.rel(server_log)
        result.outputs["sim_log"] = project.rel(sim_log)
    finally:
        pattern = shlex.quote(f"[e]asyvla.gr00t.server.*--port {port}")
        subprocess.run(["docker", "exec", gr.container, "bash", "-c", f"pkill -f {pattern}"], capture_output=True)
        server.kill()
