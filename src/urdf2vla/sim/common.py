"""プロジェクトから環境を作る共通処理（Isaac Sim 内）。"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from urdf2vla.project import Project, ProjectError
from urdf2vla.schema.camera import CamerasConfig
from urdf2vla.schema.robot import RobotConfig, RobotMetadata
from urdf2vla.schema.task import TaskConfig
from urdf2vla.schema.teleop import TeleopConfig
from urdf2vla.schema.vla import VlaInterface


@dataclass
class Configs:
    robot: RobotConfig
    cameras: CamerasConfig
    task: TaskConfig
    imported: dict
    vla: VlaInterface | None = None
    teleop: TeleopConfig | None = None
    meta: RobotMetadata | None = None
    # launch_sim が測った、物体が台の上で落ち着く向きと高さ（build/object_rest.json）
    rest: dict | None = None
    # Isaac Lab Mimic で使う環境: 物体 1 つを reset で台の上にばらつかせ、成功の終了条件と Mimic の API を持つ
    mimic: bool = False
    # Mimic の記録（datagen_info を含む Isaac Lab の HDF5）: (出力ディレクトリ, ファイル名)
    mimic_record: tuple[str, str] | None = None
    mimic_trials: int = 100  # 生成の試行回数（generation_num_trials）
    mimic_working_arm: str | None = None  # 双腕: 元のデモで物体を掴んだ腕（その腕が grasp → place、ほかの腕は止まったまま）
    # 生成の記録: (出力ディレクトリ, ファイル名)。成功の終了条件は env_cfg から取り出してここに置く（Mimic の生成ループが使う）
    mimic_generate: tuple[str, str] | None = None
    mimic_success_term: object = None


def load_configs(project: Project) -> Configs:
    report = project.build / "import_report.yaml"
    if not report.exists():
        raise ProjectError("build/import_report.yaml がありません。先に import_robot を実行してください。")
    c = Configs(
        robot=RobotConfig.model_validate(project.read_generated("robot_config.yaml")),
        cameras=CamerasConfig.model_validate(project.read_generated("cameras.yaml")),
        task=TaskConfig.model_validate(project.read_generated("task.yaml")),
        imported=yaml.safe_load(report.read_text()),
        meta=RobotMetadata.model_validate(project.read_generated("robot_metadata.yaml")),
    )
    if project.has_generated("vla_interface.yaml"):
        c.vla = VlaInterface.model_validate(project.read_generated("vla_interface.yaml"))
    if project.has_generated("teleop.yaml"):
        c.teleop = TeleopConfig.model_validate(project.read_generated("teleop.yaml"))
    rest = project.build / "object_rest.json"
    if rest.exists():
        import json

        c.rest = json.loads(rest.read_text())
    return c


def make_env(c: Configs, mode: str):
    """Isaac Lab Arena で環境を作る（urdf2vla.sim.arena）。mode は "policy" か "teleop"。"""
    from urdf2vla.sim.arena.build import make_env as make_arena_env

    return make_arena_env(c, mode)
