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
    return c


def make_env(c: Configs, mode: str):
    """Isaac Lab Arena で環境を作る（urdf2vla.sim.arena）。mode は "policy" か "teleop"。"""
    from urdf2vla.sim.arena.build import make_env as make_arena_env

    return make_arena_env(c, mode)
