"""setup_teleoperation: teleop 設定を作り、指令した方向に手先（TCP）が動くかを検査する（Isaac Sim 内）。"""

from __future__ import annotations

import torch

from urdf2vla.project import Project, dump_yaml
from urdf2vla.result import SkillResult
from urdf2vla.schema.teleop import TeleopConfig

TEST_STEPS = 8
SETTLE_STEPS = 25


def teleop_config(project: Project, c) -> TeleopConfig:
    """グリッパの開閉時間などロボットに合わせた teleop 設定。"""
    vel = {j.name: j.velocity for j in c.meta.joints}
    wait = 20
    hz = c.task.control_hz
    for g in c.robot.grippers:
        travel = max(abs(g.open[j] - g.close[j]) / max(vel.get(j) or 1.0, 1e-3) for j in g.joints)
        wait = max(wait, int(travel * hz * 1.3) + 8)
    return TeleopConfig(gripper_wait_steps=wait, max_episode_steps=int(c.task.episode_length_s * hz))


def _move(env, view, integ, c, cfg, arm: str, axis: int, sign: float) -> None:
    from urdf2vla.sim.lab import render_fix
    from urdf2vla.sim.teleop import Command

    for k in range(TEST_STEPS + SETTLE_STEPS):
        delta = [0.0] * 6
        if k < TEST_STEPS:
            delta[axis] = sign * cfg.pos_step
        env.step(integ.action(Command(arm_delta={arm: delta}), view, cfg.pos_step))
        render_fix(env, c.task)


def run(project: Project, args, result: SkillResult, app=None) -> None:
    from urdf2vla.sim.common import load_configs, make_env
    from urdf2vla.sim.lab import render_fix
    from urdf2vla.sim.state import SimView

    c = load_configs(project)
    if c.vla is None:
        result.fail("generated/vla_interface.yaml がありません。configure_vla_interface を先に実行してください。")
        return
    cfg = teleop_config(project, c)
    path = project.write_generated("teleop.yaml", cfg.model_dump(mode="json"), section="teleop")
    cfg = TeleopConfig.model_validate(project.read_generated("teleop.yaml"))

    env, env_id = make_env(c, "teleop")
    env.reset()
    view = SimView(env, c.robot, c.task, c.vla)
    print(f"[urdf2vla] teleop env: {env_id}, action dim {env.action_manager.total_action_dim}", flush=True)

    from urdf2vla.sim.teleop import TargetIntegrator

    integ = TargetIntegrator(cfg, view)
    integ.reset(view)
    checks = []
    motions = {}
    for arm in c.robot.arms:
        for axis, label in ((0, "+x"), (2, "+z"), (1, "-y")):
            sign = -1.0 if label.startswith("-") else 1.0
            p0, _ = view.tcp_pose_root(arm.name)
            # 指令を TEST_STEPS 回入れ、その後は止めて追従を待つ
            _move(env, view, integ, c, cfg, arm.name, axis, sign)
            p1, _ = view.tcp_pose_root(arm.name)
            d = (p1 - p0).tolist()
            commanded = TEST_STEPS * cfg.pos_step
            along = sign * d[axis]
            off = sum(d[k] ** 2 for k in range(3) if k != axis) ** 0.5
            ok = abs(along - commanded) < 0.25 * commanded and off < 0.25 * commanded
            motions[f"{arm.name}:{label}"] = {"commanded_m": round(commanded, 4), "moved_along_m": round(along, 4), "off_axis_m": round(off, 4)}
            checks.append({"name": f"ik_direction:{arm.name}:{label}", "ok": ok, "detail": motions[f"{arm.name}:{label}"]})
            _move(env, view, integ, c, cfg, arm.name, axis, -sign)  # 初期姿勢の近くに戻す（作業域の端で特異姿勢に入らないように）
        # 行動ラベル（IK が出した関節目標）が取れているか
        target = view.vla_vector("target")
        state = view.vla_vector("state")
        checks.append(
            {
                "name": f"action_label:{arm.name}",
                "ok": len(target) == c.vla.action.dimension and all(abs(a - b) < 0.2 for a, b in zip(target, state)),
                "detail": {"action_dim": len(target), "max_target_minus_state": round(max(abs(a - b) for a, b in zip(target, state)), 4)},
            }
        )
    report = {"env_id": env_id, "teleop": cfg.model_dump(mode="json"), "checks": checks, "motions": motions}
    (project.build / "teleop_report.yaml").write_text(dump_yaml(report))
    result.outputs["teleop"] = project.rel(path)
    result.outputs["teleop_report"] = project.rel(project.build / "teleop_report.yaml")
    result.data = {"env_id": env_id, "checks": [f"{x['name']}: {'OK' if x['ok'] else 'FAIL'}" for x in checks], "gripper_wait_steps": cfg.gripper_wait_steps}
    for x in checks:
        print(f"[urdf2vla] check {x['name']}: {'OK' if x['ok'] else 'FAIL'} {x['detail']}", flush=True)
        if not x["ok"]:
            result.fail(f"検査 {x['name']} に失敗しました: {x['detail']}")
    if result.status == "ok":
        result.next = ["collect_dataset"]
