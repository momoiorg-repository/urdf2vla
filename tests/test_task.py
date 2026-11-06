"""configure_task と TaskConfig のテスト（Isaac Sim 不要）: 物体の候補、置き場所（トレイ）、指示文、旧形式の task.yaml。"""

from __future__ import annotations

import yaml

from test_inspect import FRANKA, make_project, run
from urdf2vla.objects import DEFAULT_OBJECTS, OBJECTS
from urdf2vla.schema.task import TaskConfig


def _configure(tmp_path, capsys, task: dict | None = None) -> tuple[dict, dict]:
    proj = make_project(tmp_path, capsys, FRANKA)
    if task is not None:
        cfg = yaml.safe_load((proj / "project.yaml").read_text())
        cfg["task"] = task
        (proj / "project.yaml").write_text(yaml.safe_dump(cfg))
    for skill in ("inspect_robot", "configure_robot"):
        run(capsys, skill, "--project", str(proj))
    r = run(capsys, "configure_task", "--project", str(proj))
    path = proj / "generated/task.yaml"
    return r, (yaml.safe_load(path.read_text()) if path.exists() else {})


def test_default_task_is_objects_into_a_tray(tmp_path, capsys):
    r, task = _configure(tmp_path, capsys)
    assert r["status"] == "ok"
    t = TaskConfig.model_validate(task)
    # Franka の指の開き 8 cm に、既定の物体はすべて入る
    assert r["data"]["gripper_opening_m"] == 0.08
    assert [o.key for o in t.objects] == list(DEFAULT_OBJECTS)
    assert t.target.is_container and t.target.key == "klt_bin"
    assert t.instruction_for(0) == "put the apple in the bin"
    assert t.table.material == "oak"
    # トレイは物体を置く範囲と重ならず、台の上に収まる
    tray_left = t.target.pos[1] + t.target.size[1] / 2
    assert tray_left + max(o.grasp_width for o in t.objects) / 2 < t.spawn_y[0]
    assert abs(t.target.pos[1] - t.table.center[1]) + t.target.size[1] / 2 <= t.table.size[1] / 2
    # りんごは縮小して使い、幅がカタログの値 × 縮尺
    apple = next(o for o in t.objects if o.key == "apple")
    assert apple.scale < 1 and abs(apple.size[0] - OBJECTS["apple"].size[0] * apple.scale) < 1e-3


def test_primitive_cube_and_disc(tmp_path, capsys):
    r, task = _configure(tmp_path, capsys, {"objects": ["cube"], "container": "disc"})
    t = TaskConfig.model_validate(task)
    assert [o.key for o in t.objects] == ["cube"] and t.objects[0].asset is None
    assert not t.target.is_container
    assert t.language_instruction == "pick up the red cube and place it on the green target"


def test_unknown_object_fails(tmp_path, capsys):
    r, _ = _configure(tmp_path, capsys, {"objects": ["teapot"]})
    assert r["status"] == "error" and "teapot" in r["errors"][0]


def test_old_single_object_task_yaml_still_loads():
    old = {
        "table": {"center": [0.5, 0.0], "size": [0.7, 1.1, 0.05], "top_z": 0.0},
        "object": {"name": "cube", "size": [0.045, 0.045, 0.045], "spawn_x": [0.4, 0.5], "spawn_y": [0.06, 0.16]},
        "target": {"pos": [0.5, -0.12], "radius": 0.06},
        "language_instruction": "pick up the red cube and place it on the green target",
    }
    t = TaskConfig.model_validate(old)
    assert len(t.objects) == 1 and t.objects[0].key == "cube"
    assert t.spawn_x == [0.4, 0.5] and t.language_instruction == old["language_instruction"]
