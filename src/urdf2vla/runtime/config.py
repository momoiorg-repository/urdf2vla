"""シミュレーション実行環境の設定（runtime.yaml）とホスト↔コンテナのパス変換。"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

URDF2VLA_ROOT = Path(__file__).resolve().parents[3]
# docker/build.sh が作る image（Isaac Lab Arena / Isaac Lab 3.0 / Isaac Sim 6.2）と、その中の Python
DEFAULT_IMAGE = "urdf2vla/isaaclab_arena:b9e37c5"
DEFAULT_PYTHON = "/workspaces/isaaclab_arena/submodules/IsaacLab/isaaclab.sh -p"  # Isaac Sim の環境を読み込んでから実行する


@dataclass
class Mount:
    host: str
    container: str


@dataclass
class Runtime:
    mode: str = "docker"  # docker | local（local = このプロセスと同じマシンで Isaac Sim を直接起動）
    container: str = "urdf2vla-sim"
    image: str = DEFAULT_IMAGE
    user: str | None = None
    python: str = DEFAULT_PYTHON
    mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    setup: str = ""  # コマンドの前に実行するシェル（ライブラリパスの補正など）
    gr00t_root: str = ""  # Isaac-GR00T のソース（gr00t ランタイムのみ）

    # ---- path mapping ------------------------------------------------------
    def to_runtime(self, host_path: str | Path) -> str:
        """ホストのパスをシミュレータから見えるパスに変換する。見えなければ ValueError。"""
        p = str(Path(host_path).resolve())
        if self.mode == "local":
            return p
        for m in sorted(self.mounts, key=lambda m: -len(m.host)):
            if p == m.host or p.startswith(m.host.rstrip("/") + "/"):
                return str(PurePosixPath(m.container) / os.path.relpath(p, m.host))
        raise ValueError(
            f"{p} はコンテナ {self.container} にマウントされていません。runtime.yaml の mounts に追加してください。"
        )

    def to_host(self, runtime_path: str) -> str | None:
        """コンテナ内のパスをホストのパスに変換する（マウント外なら None）。"""
        if self.mode == "local":
            return runtime_path
        for m in sorted(self.mounts, key=lambda m: -len(m.container)):
            if runtime_path == m.container or runtime_path.startswith(m.container.rstrip("/") + "/"):
                return str(Path(m.host) / os.path.relpath(runtime_path, m.container))
        return None


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value.replace("${URDF2VLA_ROOT}", str(URDF2VLA_ROOT))))


def load_runtime(kind: str = "sim", path: str | Path | None = None) -> Runtime:
    """kind="sim": Isaac Sim（環境・teleop・記録・評価のクライアント）/ "gr00t": GR00T（変換・学習・推論サーバ）。
    gr00t は runtime.yaml の `gr00t:` セクションで、書いていない項目は sim の値を引き継ぐ。"""
    path = Path(path or os.environ.get("URDF2VLA_RUNTIME", URDF2VLA_ROOT / "runtime.yaml"))
    if not path.exists():
        return Runtime(mode="local")
    raw = yaml.safe_load(path.read_text()) or {}
    if kind != "sim":
        raw = {**{k: v for k, v in raw.items() if k != kind}, **(raw.get(kind) or {})}
    mounts = [Mount(host=_expand(m["host"]), container=m["container"]) for m in raw.get("mounts", [])]
    return Runtime(
        mode=raw.get("mode", "docker"),
        container=raw.get("container", "urdf2vla-sim"),
        image=raw.get("image", DEFAULT_IMAGE),
        # 省略時はホストのユーザ（image の entrypoint がホストと同じ uid のユーザを作る）
        user=raw.get("user") or (getpass.getuser() if raw.get("mode", "docker") == "docker" else None),
        python=raw.get("python", DEFAULT_PYTHON),
        mounts=mounts,
        env={k: str(v) for k, v in (raw.get("env") or {}).items()},
        setup=raw.get("setup", "") if kind != "sim" else "",
        gr00t_root=raw.get("gr00t_root", ""),
    )
