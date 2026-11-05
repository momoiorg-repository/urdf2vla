"""GR00T 推論サーバの起動ラッパー（urdf2vla-gr00t コンテナ内）。

run_gr00t_server.py は replay モードでしか modality config を読み込まない。学習済みモデルのモードでも
NEW_EMBODIMENT の定義が必要になりうるので、どちらのモードでも先に urdf2vla が生成した modality config を import してから起動する。
  python.sh -m urdf2vla.gr00t.server --urdf2vla-modality <urdf2vla_modality_*.py> [run_gr00t_server の引数...]
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def main() -> None:
    argv = sys.argv[1:]
    if "--urdf2vla-modality" in argv:
        i = argv.index("--urdf2vla-modality")
        path = Path(argv[i + 1])
        del argv[i : i + 2]
        sys.path.insert(0, str(path.parent))
        importlib.import_module(path.stem)  # register_modality_config(NEW_EMBODIMENT) が走る
        print(f"[urdf2vla] modality config: {path}", flush=True)

    import tyro
    from gr00t.eval.run_gr00t_server import ServerConfig
    from gr00t.eval.run_gr00t_server import main as server_main

    server_main(tyro.cli(ServerConfig, args=argv))


if __name__ == "__main__":
    main()
