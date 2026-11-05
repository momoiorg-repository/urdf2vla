"""Skill の登録簿。Agent は `urdf2vla skills --json` でこれを読み、定義済みの Skill だけを順に実行する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

RunsIn = Literal["host", "sim", "gr00t"]


@dataclass
class Arg:
    flags: tuple[str, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillSpec:
    name: str
    phase: int
    runs_in: RunsIn
    summary: str
    inputs: list[str]
    outputs: list[str]
    requires: list[str] = field(default_factory=list)
    args: list[Arg] = field(default_factory=list)
    func: Callable | None = None  # host Skill の実装（sim Skill は urdf2vla.sim 側で登録）
    # True なら needs_input でも出力は有効（質問の回答は次の Skill が取り込む）
    passable_on_needs_input: bool = False

    def satisfied(self, status: str | None) -> bool:
        return status == "ok" or (status == "needs_input" and self.passable_on_needs_input)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "phase": self.phase,
            "runs_in": self.runs_in,
            "summary": self.summary,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "requires": self.requires,
            "options": [{"flags": list(a.flags), "help": a.kwargs.get("help", "")} for a in self.args],
        }


SKILLS: dict[str, SkillSpec] = {}

# 標準の実行順（status / next が使う）
PIPELINE = [
    "inspect_robot",
    "configure_robot",
    "configure_task",
    "configure_camera",
    "import_robot",
    "launch_sim",
    "configure_vla_interface",
    "setup_teleoperation",
    "collect_dataset",
    "convert_dataset",
    "train_vla",
    "evaluate_vla",
]


def skill(
    name: str,
    *,
    phase: int,
    runs_in: RunsIn,
    summary: str,
    inputs: list[str],
    outputs: list[str],
    requires: list[str] | None = None,
    args: list[Arg] | None = None,
    passable_on_needs_input: bool = False,
):
    def deco(func: Callable | None):
        SKILLS[name] = SkillSpec(
            name=name,
            phase=phase,
            runs_in=runs_in,
            summary=summary,
            inputs=inputs,
            outputs=outputs,
            requires=requires or [],
            args=args or [],
            func=func if runs_in == "host" else None,
            passable_on_needs_input=passable_on_needs_input,
        )
        return func

    return deco


def declare_sim_skill(name: str, runs_in: RunsIn = "sim", **kwargs) -> None:
    """Isaac Sim / GR00T のコンテナ内で実装される Skill を、ホスト側の登録簿に宣言だけする。"""
    skill(name, runs_in=runs_in, **kwargs)(None)
