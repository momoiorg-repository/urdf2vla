"""urdf2vla doctor（ホスト）: 実行環境を最初に検査する。

Skill が失敗してから原因を探すより先に、よくある環境の問題を見つけて直し方を示す:
GPU ドライバ（カーネルの更新でドライバのモジュールが合わなくなった、を含む）、GPU のメモリ、Docker と
NVIDIA Container Toolkit、image とコンテナ（コンテナから GPU とリポジトリが見えるか）、ディスクの空き、
NVIDIA のアセットサーバに届くか。どれも読み取るだけで、何も変更しない。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from urdf2vla.result import SkillResult

GPU_MEMORY_MIN_GB = 12  # Isaac Sim + カメラ 3 台 + GR00T の推論サーバで約 11 GB（RTX 5080 16 GB で確かめた）
GPU_MEMORY_TRAIN_GB = 44  # train_vla がその場で学習する GPU メモリ
DRIVER_TESTED = 580  # Isaac Sim 6.2 を確かめた NVIDIA ドライバの版（クラウドの古いイメージで 535 / 550 / 570 のことがある）
DISK_WARN_GB = 50  # データセット（75 エピソードで約 4.5 GB）・USD・ログ
DISK_ERROR_GB = 10
IMAGE_DISK_GB = 40  # docker/build.sh の 2 つの image の大きさの目安


@dataclass
class Check:
    name: str
    level: str  # ok / warning / error / info
    detail: str
    fix: str = ""


def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    """コマンドを実行する。コマンドがなければ 127、時間切れなら 124（シェルと同じ）。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out"


def _has_nvidia_module(kernel_dir: Path) -> bool:
    try:
        return any(kernel_dir.rglob("nvidia.ko*"))
    except OSError:
        return False


def driver_hint(kernel: str, modules_root: Path = Path("/lib/modules"), run=_run) -> tuple[str, str]:
    """nvidia-smi が動かないときの原因と直し方。カーネルが更新され、そのカーネル用のドライバのモジュールがない場合を見分ける。"""
    others = []
    if modules_root.is_dir():
        others = sorted(k.name for k in modules_root.iterdir() if k.name != kernel and _has_nvidia_module(k))
    if others and not _has_nvidia_module(modules_root / kernel):
        detail = (
            f"起動中のカーネル {kernel} 用の NVIDIA ドライバのモジュールがありません（{', '.join(others)} にはあります）。"
            "カーネルが自動更新され、ドライバが追いついていません。"
        )
        # パターンに一致しないものがあると終了コードが 1 になるが、一致したものは出力される
        _, out, _ = run(["dpkg-query", "-W", "-f", "${Package} ${Status}\\n", "nvidia-driver-*", "linux-modules-nvidia-*"])
        pkgs = [ln.split()[0] for ln in out.splitlines() if ln.endswith(" installed")]
        driver = next((p for p in pkgs if re.fullmatch(r"nvidia-driver-\d+(-open|-server|-server-open)?", p)), None)
        # カーネルごとのパッケージ（linux-modules-nvidia-580-open-6.17.0-22-generic）ではなく、カーネルに追従するメタパッケージ
        meta = next((p for p in pkgs if re.fullmatch(r"linux-modules-nvidia-\d+(-open|-server|-server-open)?-generic(-hwe-[\d.]+)?", p)), None)
        if driver and meta:
            fix = f"ドライバとモジュールを一緒に更新して再起動します: sudo apt install {driver} {meta} && sudo reboot"
        else:
            fix = "NVIDIA ドライバと、そのカーネル用のモジュールを一緒に更新して再起動します（Ubuntu なら nvidia-driver-<版> と linux-modules-nvidia-<版>-generic-hwe-<リリース>）"
        fix += f"。急ぐときは、GRUB の Advanced options から {others[-1]} のカーネルを選んで起動しても動きます。"
        return detail, fix
    return "nvidia-smi が動きません（NVIDIA ドライバが入っていないか、読み込まれていません）。", "NVIDIA ドライバ（580 以降）を入れ、nvidia-smi が動くことを確かめてください。"


def _driver_major(version: str) -> int | None:
    m = re.match(r"(\d+)", version)
    return int(m.group(1)) if m else None


def check_gpu(run=_run, modules_root: Path = Path("/lib/modules")) -> list[Check]:
    rc, out, _ = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits"])
    if rc != 0 or not out.strip():
        detail, fix = driver_hint(platform.release(), modules_root, run)
        return [Check("nvidia_driver", "error", detail, fix)]
    gpus = []
    for line in out.strip().splitlines():
        name, driver, mem = (x.strip() for x in line.split(","))
        gpus.append((name, driver, float(mem) / 1024))
    checks = [Check("nvidia_driver", "ok", ", ".join(f"{n}（ドライバ {d}、{m:.0f} GB）" for n, d, m in gpus))]
    old = sorted({d for _, d, _ in gpus if _driver_major(d) is not None and _driver_major(d) < DRIVER_TESTED})
    if old:
        checks.append(Check(
            "driver_version", "warning",
            f"NVIDIA ドライバが {', '.join(old)} です。urdf2vla（Isaac Sim 6.2）は {DRIVER_TESTED} 以降で確かめています。古いドライバでは Isaac Sim が起動しない・描画が崩れることがあります。",
            f"ドライバを {DRIVER_TESTED} 以降に上げて再起動します（Ubuntu: sudo apt install nvidia-driver-{DRIVER_TESTED}-open && sudo reboot）。"
            f"クラウドの GPU（NVIDIA Brev など）では、{DRIVER_TESTED} 以降のドライバの入ったイメージを選ぶと早いです。",
        ))  # fmt: skip
    mem = max(m for _, _, m in gpus)
    if mem < GPU_MEMORY_MIN_GB:
        checks.append(Check("gpu_memory", "warning", f"GPU のメモリが {mem:.0f} GB です（Isaac Sim とカメラ、GR00T の推論サーバで約 11 GB を使います。16 GB で確かめています）。", "カメラの解像度や台数を減らすと動くことがあります。"))
    else:
        checks.append(Check("gpu_memory", "ok", f"{mem:.0f} GB"))
    if mem < GPU_MEMORY_TRAIN_GB:
        checks.append(Check("gpu_training", "info", f"GR00T の学習には {GPU_MEMORY_TRAIN_GB} GB 以上の GPU が要ります。train_vla は学習せず、他のマシンで実行する一式を作ります。"))
    return checks


def check_docker(runtimes, run=_run) -> list[Check]:
    if all(rt.mode == "local" for rt in runtimes):
        return [Check("docker", "info", "runtime.yaml が mode: local なので、Docker は使いません。")]
    rc, out, err = run(["docker", "info", "--format", "{{json .}}"])
    if rc == 127:
        return [Check("docker", "error", "docker コマンドがありません。", "Docker Engine を入れてください（https://docs.docker.com/engine/install/）。")]
    if rc != 0:
        if "permission denied" in err.lower():
            return [Check("docker", "error", "Docker を使う権限がありません。", "sudo usermod -aG docker $USER を実行し、ログインし直してください。")]
        return [Check("docker", "error", f"Docker のデーモンに接続できません: {err.strip()[:200]}", "sudo systemctl start docker")]
    checks = [Check("docker", "ok", "Docker のデーモンに接続できます")]
    if shutil.which("nvidia-container-cli") or shutil.which("nvidia-ctk"):
        checks.append(Check("nvidia_container_toolkit", "ok", "NVIDIA Container Toolkit があります"))
    else:
        checks.append(Check("nvidia_container_toolkit", "error", "NVIDIA Container Toolkit がありません（コンテナから GPU を使えません）。",
                            "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html の手順で入れてください。"))  # fmt: skip
    try:
        info = json.loads(out)
    except json.JSONDecodeError:
        info = {}
    missing_images = 0
    for rt in runtimes:
        kind = "gr00t" if "gr00t" in rt.container else "sim"
        build = "bash docker/build.sh" + (" gr00t" if kind == "gr00t" else "")
        start = "bash docker/run.sh" + (" gr00t" if kind == "gr00t" else "")
        # GR00T は変換・学習・評価（Phase 3〜4）で使う。シミュレーションだけなら無くてよい
        level = "error" if kind == "sim" else "warning"
        if run(["docker", "image", "inspect", rt.image])[0] != 0:
            missing_images += 1
            checks.append(Check(f"image:{kind}", level, f"image {rt.image} がありません。", f"{build}（初回は 30〜60 分かかります）"))
            continue
        checks.append(Check(f"image:{kind}", "ok", rt.image))
        rc, out2, _ = run(["docker", "inspect", "-f", "{{.State.Running}}", rt.container])
        if rc != 0 or out2.strip() != "true":
            checks.append(Check(f"container:{kind}", "warning", f"コンテナ {rt.container} が{'止まっています' if rc == 0 else 'ありません'}。", start))
            continue
        checks.append(Check(f"container:{kind}", "ok", f"{rt.container} が動いています"))
        if run(["docker", "exec", rt.container, "nvidia-smi", "-L"])[0] != 0:
            checks.append(Check(f"container_gpu:{kind}", "error", f"コンテナ {rt.container} から GPU が見えません。",
                                f"ホストの nvidia-smi が動くことを確かめてから、{start} --rm と {start} で作り直してください。"))  # fmt: skip
        else:
            checks.append(Check(f"container_gpu:{kind}", "ok", "コンテナから GPU が見えます"))
        for m in rt.mounts:
            if run(["docker", "exec", rt.container, "test", "-d", f"{m.container.rstrip('/')}/src/urdf2vla"])[0] != 0:
                checks.append(Check(f"container_mount:{kind}", "error", f"コンテナ {rt.container} の {m.container} に urdf2vla のリポジトリが見えません。",
                                    f"{start} --rm と {start} で、このリポジトリをマウントして作り直してください。"))  # fmt: skip
                break
        else:
            checks.append(Check(f"container_mount:{kind}", "ok", "コンテナからリポジトリが見えます"))
    root = info.get("DockerRootDir")
    if missing_images and root:
        free = _free_gb(Path(root))
        if free is not None and free < IMAGE_DISK_GB:
            checks.append(Check("docker_disk", "error", f"Docker の保存先（{root}）の空きが {free:.0f} GB です（image に約 {IMAGE_DISK_GB} GB 要ります）。", "空きを作ってから docker/build.sh を実行してください。"))
    return checks


def _free_gb(path: Path) -> float | None:
    for p in (path, *path.parents):  # 読めない場所（/var/lib/docker）は親で測る
        try:
            return shutil.disk_usage(p).free / 1e9
        except OSError:
            continue
    return None


def check_disk(repo: Path) -> list[Check]:
    free = _free_gb(repo)
    if free is None:
        return []
    if free < DISK_ERROR_GB:
        return [Check("disk", "error", f"リポジトリのディスクの空きが {free:.0f} GB です。", "データセットやログ（projects/*/datasets、logs）を消して空きを作ってください。")]
    if free < DISK_WARN_GB:
        return [Check("disk", "warning", f"リポジトリのディスクの空きが {free:.0f} GB です（データセットは 75 エピソードで約 4.5 GB）。", "")]
    return [Check("disk", "ok", f"空き {free:.0f} GB")]


def check_assets(url: str | None = None, timeout: float = 10.0) -> list[Check]:
    from urdf2vla.grippers import ISAAC_ASSETS_ROOT

    url = url or f"{ISAAC_ASSETS_ROOT}/Isaac/Robots/Robotiq/2F-85/LICENSE"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout) as r:
            ok = r.status == 200
    except OSError:
        ok = False
    if ok:
        return [Check("asset_server", "ok", "NVIDIA のアセットサーバに届きます")]
    return [Check("asset_server", "warning", "NVIDIA のアセットサーバに届きません。物体・グリッパ・台のアセットは初回にここからダウンロードします。",
                  "ネットワーク（プロキシ）を確かめてください。一度ダウンロードしたアセットはキャッシュから読みます。")]  # fmt: skip


def check_python() -> list[Check]:
    checks = []
    try:
        import viser  # noqa: F401

        checks.append(Check("view", "ok", "urdf2vla view を使えます"))
    except ImportError:
        checks.append(Check("view", "info", "urdf2vla view（ブラウザでロボットを確かめる画面）には uv pip install -e '.[view]' が要ります。"))
    return checks


def run_doctor(repo: Path, runtimes, run=_run, assets: bool = True) -> SkillResult:
    result = SkillResult(skill="doctor")
    checks = [*check_gpu(run), *check_docker(runtimes, run), *check_disk(repo), *(check_assets() if assets else []), *check_python()]
    for c in checks:
        if c.level == "error":
            result.fail(f"{c.name}: {c.detail}" + (f" → {c.fix}" if c.fix else ""))
        elif c.level == "warning":
            result.warnings.append(f"{c.name}: {c.detail}" + (f" → {c.fix}" if c.fix else ""))
    result.data = {
        "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "checks": [f"{c.name}: {c.level.upper() if c.level != 'ok' else 'OK'} {c.detail}" for c in checks],
    }
    if result.status == "ok":
        result.next = ["init"]
    return result


def default_runtimes():
    from urdf2vla.runtime.config import load_runtime

    out = []
    for kind in ("sim", "gr00t"):
        try:
            out.append(load_runtime(kind))
        except Exception:  # noqa: BLE001  runtime.yaml の書き間違いは各 Skill がエラーにする
            continue
    return out


if __name__ == "__main__":  # pragma: no cover
    print(run_doctor(Path(os.getcwd()), default_runtimes()).to_json())
