"""generate_mimic（ホスト）: Isaac Lab Mimic で、少数の元のデモから物体の位置を変えたデモを生成して記録する。

3 つの工程を Isaac Sim で順に実行する（それぞれ別のプロセス）:
  1. mimic_source:   元のデモを集める（Mimic の datagen_info 付き。datasets/<name>_mimic_src.hdf5）。--source-device で
                     scripted（スクリプト）か、人の操作（socket: 別の端末の `urdf2vla teleop-keys` / keyboard）
双腕では、元のデモで物体を掴んだ腕（デモごとに同じ腕）が作業する腕になり、もう一方の腕は止まったまま写す。
  2. mimic_generate: Isaac Lab Mimic の生成を --trials 回試す（成功分を datasets/<name>_mimic_gen.hdf5）
  3. collect_dataset --device mimic: 生成したエピソードを再生し、画像付きの urdf2vla のデータセットに記録する
"""

from __future__ import annotations

from urdf2vla.project import Project
from urdf2vla.registry import Arg, skill
from urdf2vla.result import SkillResult
from urdf2vla.runtime.config import load_runtime
from urdf2vla.runtime.dispatch import run_sim_skill
from urdf2vla.schema.robot import RobotConfig
from urdf2vla.schema.task import TaskConfig


@skill(
    "generate_mimic",
    phase=2,
    runs_in="host",
    summary="Isaac Lab Mimic で、少数の元のデモ（スクリプトか人の操作）から物体の位置を変えたデモを --trials 回生成し、成功分を記録する（物体 1 つ + 容器のタスク。単腕・双腕）",
    inputs=["generated/teleop.yaml", "generated/vla_interface.yaml"],
    outputs=["datasets/<name>_mimic_src.hdf5", "datasets/<name>_mimic_gen.hdf5", "datasets/<name>.hdf5"],
    requires=["setup_teleoperation"],
    args=[
        Arg(("--source-demos",), {"type": int, "default": 10, "help": "集める元のデモの数"}),
        Arg(("--source-device",), {"choices": ["scripted", "socket", "keyboard"], "default": "scripted",
                                   "help": "元のデモを集める装置: scripted（スクリプト）/ socket（別の端末の urdf2vla teleop-keys）/ keyboard"}),  # fmt: skip
        Arg(("--trials",), {"type": int, "default": 100, "help": "Mimic の生成を試す回数（generation_num_trials。成功するまで続けない）"}),
        Arg(("--dataset",), {"default": "demos", "help": "datasets/<名前>.hdf5"}),
        Arg(("--seed",), {"type": int, "default": 0}),
        Arg(("--timeout",), {"type": float, "default": 7200.0, "help": "工程ごとの秒数の上限"}),
    ],
)
def generate_mimic(project: Project, opts: dict, result: SkillResult, log) -> None:
    robot = RobotConfig.model_validate(project.read_generated("robot_config.yaml"))
    if len(robot.arms) > 2:
        result.fail("Mimic は腕が 1 本か 2 本のロボットで使えます。")
        return
    if not robot.grippers:
        result.fail("Mimic の Pick & Place にはグリッパが要ります。")
        return
    task = TaskConfig.model_validate(project.read_generated("task.yaml"))
    if len(task.objects) != 1 or not task.target.is_container:
        result.fail("Mimic は物体 1 つと容器のタスクで使います。project.yaml の task.objects を 1 つ（例: [color_cube]）にし、configure_task からやり直してください。")
        return
    sim = load_runtime("sim")
    ds, timeout = opts["dataset"], opts.get("timeout")
    base = ["--dataset", ds, "--seed", str(opts["seed"])]
    steps = [
        ("mimic_source", base + ["--source-demos", str(opts["source_demos"]), "--source-device", opts.get("source_device") or "scripted"]),
        ("mimic_generate", base + ["--trials", str(opts["trials"])]),
        ("collect_dataset", ["--device", "mimic", "--source", f"datasets/{ds}_mimic_gen.hdf5", "--dataset", ds, "--seed", str(opts["seed"])]),
    ]  # fmt: skip
    data: dict = {}
    for name, extra in steps:
        if name == "collect_dataset":  # 生成で成功したエピソードをすべて再生する
            n = int(data["mimic_generate"].get("generated_successes") or 0)
            extra = extra + ["--num-demos", str(n), "--max-attempts", str(n)]
        log.info("%s %s", name, " ".join(extra))
        sim_log = project.new_log(name)
        r = run_sim_skill(sim, name, project, extra, sim_log, timeout)
        result.outputs.update(r.outputs)
        result.outputs[f"{name}_log"] = project.rel(sim_log)
        result.warnings += r.warnings
        data[name] = r.data
        if r.status == "error":
            result.errors += [f"{name}: {e}" for e in r.errors]
            result.status = "error"
            break
    result.data = data
    if result.status != "error":
        gen = data.get("mimic_generate", {})
        result.data["summary"] = {
            "source_demos": data.get("mimic_source", {}).get("saved"),
            "trials": gen.get("trials"),
            "generated_successes": gen.get("generated_successes"),
            "recorded_demos": data.get("collect_dataset", {}).get("saved"),
        }
        result.next = ["convert_dataset"]
