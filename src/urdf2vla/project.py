"""プロジェクトディレクトリ（1 ロボット = 1 ディレクトリ）の読み書き。

projects/<name>/
  project.yaml     ユーザが書く入力と上書き設定（コメントを保持するため urdf2vla は書き換えない）
  answers.yaml     質問への回答（`urdf2vla answer` が追記。手編集も可）
  generated/       派生物（毎回再生成。手編集しない — 変更は project.yaml の overrides へ）
  build/           正規化 URDF・USD・import レポート
  logs/            Skill ごとのログと結果 JSON
  snapshots/       カメラ画像
  state.json       工程ごとの状態
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import yaml

from urdf2vla.result import SkillResult

GENERATED_HEADER = (
    "# このファイルは urdf2vla が自動生成します。直接編集しないでください。\n"
    "# 変更したい値は project.yaml の overrides: に書くと、次回の生成で反映されます。\n"
)


class ProjectError(RuntimeError):
    pass


class _Dumper(yaml.SafeDumper):
    """数値や短い文字列だけのリストは 1 行で書く（読みやすさのため）。"""


def _repr_list(dumper: yaml.SafeDumper, data: list):
    flow = all(isinstance(v, (int, float, bool)) or v is None for v in data) and len(data) <= 16
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(list, _repr_list)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=120)


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not (self.root / "project.yaml").exists():
            raise ProjectError(f"project.yaml が見つかりません: {self.root}（先に `urdf2vla init` を実行してください）")

    # ---- paths -------------------------------------------------------------
    @property
    def generated(self) -> Path:
        return self.root / "generated"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    def path(self, value: str | Path) -> Path:
        """project.yaml 内の相対パスはプロジェクトディレクトリ基準で解決する。"""
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p).resolve()

    def rel(self, path: str | Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    # ---- user files --------------------------------------------------------
    def config(self) -> dict[str, Any]:
        return yaml.safe_load((self.root / "project.yaml").read_text()) or {}

    @property
    def name(self) -> str:
        return self.config().get("name") or self.root.name

    def answers(self) -> dict[str, Any]:
        f = self.root / "answers.yaml"
        if not f.exists():
            return {}
        return yaml.safe_load(f.read_text()) or {}

    def set_answers(self, values: dict[str, Any]) -> None:
        merged = {**self.answers(), **values}
        body = yaml.safe_dump(merged, allow_unicode=True, sort_keys=True)
        (self.root / "answers.yaml").write_text("# urdf2vla の質問への回答（`urdf2vla answer` で追記、手編集も可）\n" + body)

    def clear_answers(self, keys: list[str]) -> None:
        """回答を消す（`urdf2vla view` で保存したカメラを消して project.yaml の指定に戻すときなど）。"""
        kept = {k: v for k, v in self.answers().items() if k not in keys}
        body = yaml.safe_dump(kept, allow_unicode=True, sort_keys=True) if kept else ""
        (self.root / "answers.yaml").write_text("# urdf2vla の質問への回答（`urdf2vla answer` で追記、手編集も可）\n" + body)

    def overrides(self, section: str) -> dict[str, Any]:
        return (self.config().get("overrides") or {}).get(section) or {}

    # ---- generated files ---------------------------------------------------
    def write_generated(self, name: str, data: dict[str, Any], section: str | None = None) -> Path:
        """生成物を書き出す。section を渡すと project.yaml の overrides[section] を上から適用する。"""
        if section:
            data = deep_merge(data, self.overrides(section))
        self.generated.mkdir(parents=True, exist_ok=True)
        path = self.generated / name
        path.write_text(GENERATED_HEADER + dump_yaml(data))
        return path

    def read_generated(self, name: str) -> dict[str, Any]:
        path = self.generated / name
        if not path.exists():
            raise ProjectError(f"{self.rel(path)} がありません。前の工程を先に実行してください。")
        return yaml.safe_load(path.read_text()) or {}

    def has_generated(self, name: str) -> bool:
        return (self.generated / name).exists()

    # ---- state & logs ------------------------------------------------------
    def state(self) -> dict[str, Any]:
        f = self.root / "state.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def record(self, result: SkillResult, inputs_hash: str | None = None) -> None:
        state = self.state()
        state[result.skill] = {
            "status": result.status,
            "finished_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "inputs_hash": inputs_hash,
            "log": result.log,
        }
        (self.root / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2))
        self.logs.mkdir(parents=True, exist_ok=True)
        (self.logs / f"latest_{result.skill}.json").write_text(result.to_json())

    def new_log(self, skill: str) -> Path:
        self.logs.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        return self.logs / f"{stamp}_{skill}.log"


def file_hash(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def open_logger(log_path: Path, name: str) -> logging.Logger:
    logger = logging.getLogger(f"urdf2vla.{name}.{log_path.stem}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
