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
DEFAULT_BASE_MODEL = "nvidia/GR00T-N1.7-3B"


def _args(base: str, dataset: str, modality: str, output: str, a) -> list[str]:
    return [
        "--base-model-path", base,
        "--dataset-path", dataset,
        "--embodiment-tag", "new_embodiment",
        "--modality-config-path", modality,
        "--output-dir", output,
        "--max-steps", str(a.max_steps),
        "--global-batch-size", str(a.batch_size),
        "--save-steps", str(a.save_steps),
        "--save-total-limit", "5",
        "--dataloader-num-workers", "2",
        "--no-tune-llm", "--no-tune-visual", "--tune-projector", "--tune-diffusion-model",
    ]  # fmt: skip


def _script(job: str, args_tmpl: list[str]) -> str:
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
#             Hugging Face にログイン済み（ベースモデル {DEFAULT_BASE_MODEL} を取得するため）
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
BASE_MODEL="${{BASE_MODEL:-{DEFAULT_BASE_MODEL}}}"
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
    mods = sorted([*ds.glob("urdf2vla_modality_*.py"), *ds.glob("easyvla_modality_*.py")]) if ds.exists() else []
    if not mods:
        result.fail(f"{project.rel(ds)} がありません。convert_dataset --dataset {args.dataset} を先に実行してください。")
        return
    modality = mods[0]
    job = args.job or f"{project.name}_{args.dataset}"
    job_dir = project.root / "training" / job
    job_dir.mkdir(parents=True, exist_ok=True)

    # ジョブ一式（どのマシンでも動くよう、パスは環境変数で差し替えられる）
    tmpl = _args("$BASE_MODEL", "$DATASET_DIR", f"$DATASET_DIR/{modality.name}", "$OUTPUT_DIR", args)
    script = job_dir / "train.sh"
    script.write_text(_script(job, tmpl))
    script.chmod(0o755)

    # 引数が GR00T のランチャで正しく解釈されるか（学習は始めない）
    local_args = _args(args.base_model or DEFAULT_BASE_MODEL, str(ds), str(modality), str(job_dir / "checkpoints"), args)
    tyro.cli(FinetuneConfig, args=local_args)
    checks = [{"name": "finetune_args_valid", "ok": True, "detail": "GR00T の FinetuneConfig として解釈できた"}]

    gpu_gb = torch.cuda.get_device_properties(0).total_memory / 1e9 if torch.cuda.is_available() else 0.0
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "なし"
    can_train = gpu_gb >= REQUIRED_GB or args.force_local
    report = {
        "job": job,
        "dataset": project.rel(ds),
        "script": project.rel(script),
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
        env = dict(os.environ, DATASET_DIR=str(ds), OUTPUT_DIR=str(job_dir / "checkpoints"),
                   BASE_MODEL=args.base_model or DEFAULT_BASE_MODEL, GR00T_ROOT=os.environ["URDF2VLA_GR00T_ROOT"],
                   GR00T_PYTHON=sys_python())  # fmt: skip
        rc = subprocess.call(["bash", str(script)], env=env)
        report["exit_code"] = rc
        if rc != 0:
            result.fail(f"学習が失敗しました（exit {rc}）。ログを確認してください。")
        result.outputs["checkpoints"] = project.rel(job_dir / "checkpoints")
    (project.build / "train_report.yaml").write_text(dump_yaml(report))
    result.outputs["train_script"] = project.rel(script)
    result.outputs["train_report"] = project.rel(project.build / "train_report.yaml")
    result.data = {k: report[k] for k in ("job", "mode", "gpu")}
    result.data["checks"] = [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'}" for c in checks]
    if result.status == "ok":
        result.next = ["evaluate_vla"]


def sys_python() -> str:
    import sys

    return sys.executable
