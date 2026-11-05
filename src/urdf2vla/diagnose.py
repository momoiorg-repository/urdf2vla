"""ログから既知の失敗パターンを探し、原因と対処を返す。

Agent はここで `auto_fix` が付いた対処だけを自動で試してよい。
ロボットの事実（End Effector・関節・グリッパ）に関わる対処はユーザに確認する。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Pattern:
    id: str
    regex: str
    cause: str
    fix: str
    auto_fix: bool = False


PATTERNS = [
    Pattern(
        "container_down",
        r"コンテナ .* が起動していません|No such container|is not running",
        "シミュレーション用コンテナが動いていません。",
        "`bash docker/run.sh` でコンテナを起動して再実行します。",
        auto_fix=True,
    ),
    Pattern(
        "glx_fbconfig",
        r"GLXBadFBConfig",
        "GUI 表示用の OpenGL 設定がホストの GPU と合っていません（ハイブリッド GPU で起きやすい）。",
        "`__NV_PRIME_RENDER_OFFLOAD=1` と `__GLX_VENDOR_LIBRARY_NAME=nvidia` を runtime.yaml の env に追加するか、headless / livestream で起動します。",
        auto_fix=True,
    ),
    Pattern(
        "importer_download",
        r"isaacsim\.asset\.importer\.urdf-2\.4\.31.*(fail|error|not found)|Failed to (download|resolve) extension",
        "URDF importer 拡張（2.4.31）を初回取得できませんでした。",
        "ネットワーク接続を確認して再実行します（取得後は urdf2vla-ov-data volume にキャッシュされます）。",
    ),
    Pattern(
        "mesh_missing",
        r"(Failed to|Unable to|Cannot) (open|load|find|resolve).*\.(stl|dae|obj)",
        "URDF が参照するメッシュをシミュレータ側から読めません。",
        "inspect_robot の meshes を確認し、メッシュのあるディレクトリが runtime.yaml の mounts に含まれているか確認します。",
    ),
    Pattern(
        "joint_mismatch",
        r"joint_name_match.*(FAIL|失敗)|Not all actuators are configured",
        "USD の関節と robot_config の関節が一致していません。",
        "import_robot の checks.joint_name_match を確認し、URDF を変更した場合は inspect_robot からやり直します。",
    ),
    Pattern(
        "no_articulation",
        r"Failed to find (an )?articulation|ArticulationRootAPI|articulation root",
        "USD に articulation root がありません。",
        "configure_robot の root_link を確認します（空の root link を外したか）。",
    ),
    Pattern(
        "diverged",
        r"no_nan.*(FAIL|失敗)|diverg",
        "シミュレーションが発散しました（関節ゲインが強すぎる、自己衝突、質量のない link など）。",
        "project.yaml の overrides.robot.actuators でゲインを下げる、asset.self_collision を false にする、などを試します。",
    ),
    Pattern(
        "tracking",
        r"default_pose_tracking.*(FAIL|失敗)",
        "初期姿勢に追従できていません（ゲイン不足・関節の可動範囲・自己衝突）。",
        "import_robot の checks を見て、該当関節の stiffness を上げるか default_pose を見直します。",
    ),
    Pattern(
        "camera_frozen",
        r"camera_alive.*(FAIL|失敗)|frozen_steps.: [1-9]",
        "カメラ画像が step 間で変化していません（env.step 内の描画で RTX カメラが更新されていない）。",
        "project.yaml の overrides.task に render_fix: true を書き、configure_task から再実行します"
        "（1 制御 step ごとに物理を 1 step 余分に進めるため、制御周期が task.control_hz の値に変わります）。",
    ),
    Pattern(
        "cuda_oom",
        r"CUDA out of memory|OutOfMemoryError",
        "GPU メモリ不足です。",
        "他の GPU プロセス（既存コンテナの学習など）を確認し、カメラ解像度や num_envs を下げます。",
    ),
    Pattern(
        "timeout",
        r"秒以内に終わりませんでした",
        "Isaac Sim が時間内に終わりませんでした（初回はシェーダのコンパイルで数分かかることがあります）。",
        "--timeout を延ばして再実行し、ログ末尾で止まっている箇所を確認します。",
        auto_fix=True,
    ),
]


def diagnose_text(text: str) -> list[dict]:
    hits = []
    for p in PATTERNS:
        m = re.search(p.regex, text, flags=re.IGNORECASE)
        if m:
            d = asdict(p)
            d.pop("regex")
            d["evidence"] = text[max(0, m.start() - 120) : m.end() + 120].strip()
            hits.append(d)
    return hits


def tail(path: Path, n: int = 60) -> str:
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:])
