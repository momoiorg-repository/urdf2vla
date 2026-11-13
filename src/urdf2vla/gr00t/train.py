"""train_vla: GR00T N1.7 の fine-tune ジョブを作り、GPU が足りればそのまま学習する（urdf2vla-gr00t コンテナ内）。

GR00T N1.7（3B）は LLM と画像エンコーダを凍結しても学習に約 42GB 使う（A40 での実測）。
16GB の GPU では学習できないので、その場合は他のマシン（クラウド）で実行できるジョブ一式を作って止める。
Hugging Face への push は行わない。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tarfile
from pathlib import Path

from urdf2vla.project import Project, dump_yaml
from urdf2vla.result import SkillResult

# 学習に使う image（docker/build.sh が Isaac-GR00T 23ace64 から作るもの。train.sh の使い方に書く）
GR00T_IMAGE = "urdf2vla/gr00t:23ace64"
REQUIRED_GB = 44.0
# checkpoint 1 つ = 重み約 11 GB + optimizer の状態約 13 GB（L40S での実測）。save_total_limit 個まで残る
CHECKPOINT_GB = 24.0
DEFAULT_BASE_MODEL = "nvidia/GR00T-N1.7-3B"
# 単腕のロボット（Franka・FANUC など）は DROID（単腕 + 外部カメラ + 手先カメラ）で学習したモデルから fine-tune する
SINGLE_ARM_BASE_MODEL = "nvidia/GR00T-N1.7-DROID"


def default_base_model(layout: dict, arms: int | None) -> str:
    """変換済みデータの並びと腕の数からベースモデルを決める（DROID の並び・単腕 → DROID、双腕 → 3B）。"""
    if layout.get("layout") == "droid" or (arms or layout.get("arms")) == 1:
        return SINGLE_ARM_BASE_MODEL
    return DEFAULT_BASE_MODEL


def _args(base: str, dataset: str, modality: str, output: str, a, tag: str = "new_embodiment") -> list[str]:
    return [
        "--base-model-path", base,
        "--dataset-path", dataset,
        "--embodiment-tag", tag,
        "--modality-config-path", modality,
        "--output-dir", output,
        "--max-steps", str(a.max_steps),
        "--global-batch-size", str(a.batch_size),
        "--save-steps", str(a.save_steps),
        "--save-total-limit", str(getattr(a, "save_total_limit", 2)),
        "--dataloader-num-workers", "2",
        "--no-tune-llm", "--no-tune-visual", "--tune-projector", "--tune-diffusion-model",
    ]  # fmt: skip


def _script(job: str, args_tmpl: list[str], base: str = DEFAULT_BASE_MODEL) -> str:
    q = [shlex.quote(a) if not a.startswith("$") else f'"{a}"' for a in args_tmpl]
    lines, i = [], 0
    while i < len(q):  # "--flag 値" を 1 行にまとめる
        if i + 1 < len(q) and not q[i + 1].startswith("--"):
            lines.append(f"{q[i]} {q[i + 1]}")
            i += 2
        else:
            lines.append(q[i])
            i += 1
    quoted = " \\\n  ".join(lines)
    return f"""#!/usr/bin/env bash
# urdf2vla が生成した GR00T N1.7 fine-tune ジョブ: {job}
# 必要なもの: GPU メモリ {REQUIRED_GB:.0f}GB 以上（A100 / H100 / L40S / A6000 など）、Isaac-GR00T（N1.7）の環境、
#             Hugging Face にログイン済み（ベースモデル {base} を取得するため）
# 使い方（urdf2vla の GR00T image。学習するマシンで bash docker/build.sh gr00t で作るか、docker save / docker load で持っていく）:
#   tar -xzf {job}.tar.gz && cd {job}
#   docker run --rm --gpus all --shm-size=16g -u "$(id -u):$(id -g)" -e USER="$(id -un)" -e HOME=/tmp \\
#     -e HF_HOME=/hf -v "$HOME/.cache/huggingface":/hf -v "$PWD":/job {GR00T_IMAGE} bash /job/train.sh
#   （-u はホストのユーザで実行し、checkpoint と Hugging Face のキャッシュを root の所有にしないため）
# Isaac-GR00T を直接入れた環境なら: GR00T_ROOT=/path/to/Isaac-GR00T GR00T_PYTHON=python bash train.sh
set -euo pipefail
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
# urdf2vla の GR00T image では Isaac-GR00T が /workspace、その依存が /workspace/.venv にある
if [[ -z "${{GR00T_ROOT:-}}" && -f /workspace/gr00t/experiment/launch_finetune.py ]]; then GR00T_ROOT=/workspace; fi
GR00T_ROOT="${{GR00T_ROOT:?Isaac-GR00T のパスを GR00T_ROOT に設定してください}}"
# PYTHON ではなく GR00T_PYTHON（GR00T の image は環境変数 PYTHON=/usr/bin/python を持っていて、依存の入った環境ではない）
if [[ -z "${{GR00T_PYTHON:-}}" && -x /workspace/.venv/bin/python && "$GR00T_ROOT" == /workspace ]]; then GR00T_PYTHON=/workspace/.venv/bin/python; fi
DATASET_DIR="${{DATASET_DIR:-$HERE/dataset}}"
OUTPUT_DIR="${{OUTPUT_DIR:-$HERE/checkpoints}}"
BASE_MODEL="${{BASE_MODEL:-{base}}}"
GR00T_PYTHON="${{GR00T_PYTHON:-python}}"
"$GR00T_PYTHON" "$GR00T_ROOT/gr00t/experiment/launch_finetune.py" \\
  {quoted}
echo "checkpoints: $OUTPUT_DIR（urdf2vla の evaluate_vla --model に checkpoint-N のパスを渡して評価します）"
"""


def run(project: Project, args, result: SkillResult) -> None:
    import torch
    import tyro

    from gr00t.configs.finetune_config import FinetuneConfig

    ds = project.root / "datasets" / f"{args.dataset}_gr00t"
    # 旧名（easyVLA）で変換したデータは easyvla_modality_*.py を持つ
    mods = sorted([*ds.glob("urdf2vla_modality_*.py"), *ds.glob("easyvla_modality_*.py"), *ds.glob("urdf2vla_droid_modality.py")]) if ds.exists() else []
    if not mods:
        result.fail(f"{project.rel(ds)} がありません。convert_dataset --dataset {args.dataset} を先に実行してください。")
        return
    from urdf2vla.gr00t.convert import dataset_layout

    layout = dataset_layout(ds)
    if layout.get("layout") == "droid":
        from urdf2vla.gr00t.droid import DROID_TAG

        tag, modality = DROID_TAG, ds / "urdf2vla_droid_modality.py"
        arms = 1
    else:
        tag, modality = "new_embodiment", mods[0]
        arms = layout.get("arms") or _project_arms(project)
    base = args.base_model or default_base_model(layout, arms)
    job = args.job or f"{project.name}_{args.dataset}"
    job_dir = project.root / "training" / job
    job_dir.mkdir(parents=True, exist_ok=True)

    # ジョブ一式（どのマシンでも動くよう、パスは環境変数で差し替えられる）
    tmpl = _args("$BASE_MODEL", "$DATASET_DIR", f"$DATASET_DIR/{modality.name}", "$OUTPUT_DIR", args, tag)
    script = job_dir / "train.sh"
    script.write_text(_script(job, tmpl, base))
    script.chmod(0o755)

    # 引数が GR00T のランチャで正しく解釈されるか（学習は始めない）
    local_args = _args(base, str(ds), str(modality), str(job_dir / "checkpoints"), args, tag)
    tyro.cli(FinetuneConfig, args=local_args)
    checks = [{"name": "finetune_args_valid", "ok": True, "detail": "GR00T の FinetuneConfig として解釈できた"}]

    gpu_gb = torch.cuda.get_device_properties(0).total_memory / 1e9 if torch.cuda.is_available() else 0.0
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "なし"
    can_train = gpu_gb >= REQUIRED_GB or args.force_local
    report = {
        "job": job,
        "dataset": project.rel(ds),
        "script": project.rel(script),
        "base_model": base,
        "embodiment_tag": tag,
        "layout": layout,
        "gpu": {"name": gpu_name, "memory_gb": round(gpu_gb, 1), "required_gb": REQUIRED_GB},
        "hyperparameters": {"max_steps": args.max_steps, "global_batch_size": args.batch_size, "save_steps": args.save_steps,
                            "tune": "projector + diffusion（LLM・画像エンコーダは凍結）"},
        "checks": checks,
    }  # fmt: skip

    if not can_train:
        pack = job_dir.parent / f"{job}.tar.gz"
        with tarfile.open(pack, "w:gz") as tar:
            tar.add(script, arcname=f"{job}/train.sh")
            tar.add(ds, arcname=f"{job}/dataset")
        report["mode"] = "packaged"
        report["package"] = project.rel(pack)
        result.outputs["package"] = project.rel(pack)
        result.warnings.append(
            f"この GPU（{gpu_name}, {gpu_gb:.0f}GB）では GR00T N1.7 を学習できません（{REQUIRED_GB:.0f}GB 以上が必要）。"
            f"{project.rel(pack)} を大きな GPU のマシンにコピーし、展開して train.sh を実行してください。"
            "学習後の checkpoint は evaluate_vla --model で評価できます。"
        )
    else:
        report["mode"] = "local"
        import shutil

        free_gb = shutil.disk_usage(job_dir).free / 1e9
        need_gb = CHECKPOINT_GB * (getattr(args, "save_total_limit", 2) + 1)  # 保存の途中は 1 つ多い
        report["disk"] = {"free_gb": round(free_gb, 1), "needed_gb": need_gb}
        if free_gb < need_gb:
            result.fail(
                f"{project.rel(job_dir)} のディスクの空きが {free_gb:.0f} GB です。checkpoint は 1 つ約 {CHECKPOINT_GB:.0f} GB で、"
                f"--save-total-limit {getattr(args, 'save_total_limit', 2)} では約 {need_gb:.0f} GB 要ります（空きを作るか limit を下げる）。"
            )
            (project.build / "train_report.yaml").write_text(dump_yaml(report))
            return
        env = dict(os.environ, DATASET_DIR=str(ds), OUTPUT_DIR=str(job_dir / "checkpoints"),
                   BASE_MODEL=base, GR00T_ROOT=os.environ["URDF2VLA_GR00T_ROOT"],
                   GR00T_PYTHON=sys_python())  # fmt: skip
        rc = subprocess.call(["bash", str(script)], env=env)
        report["exit_code"] = rc
        if rc != 0:
            result.fail(f"学習が失敗しました（exit {rc}）。ログを確認してください。")
        result.outputs["checkpoints"] = project.rel(job_dir / "checkpoints")
    (project.build / "train_report.yaml").write_text(dump_yaml(report))
    result.outputs["train_script"] = project.rel(script)
    result.outputs["train_report"] = project.rel(project.build / "train_report.yaml")
    result.data = {k: report[k] for k in ("job", "mode", "gpu", "base_model", "embodiment_tag")}
    result.data["checks"] = [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'}" for c in checks]
    if result.status == "ok":
        result.next = ["evaluate_vla"]


def _project_arms(project: Project) -> int | None:
    """古い変換（meta/urdf2vla.json がない）用: プロジェクトの vla_interface の腕の数。"""
    import yaml

    p = project.root / "generated" / "vla_interface.yaml"
    if not p.exists():
        return None
    groups = (yaml.safe_load(p.read_text()) or {}).get("action", {}).get("groups", [])
    return sum(1 for g in groups if g.get("kind") == "joint_position") or None


def sys_python() -> str:
    import sys

    return sys.executable
