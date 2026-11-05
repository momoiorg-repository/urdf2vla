"""import_robot: URDF→USD 変換と、ロボット単体での読み込み検査（Isaac Sim 内）。"""

from __future__ import annotations

from urdf2vla.project import Project, dump_yaml
from urdf2vla.result import SkillResult
from urdf2vla.schema.robot import RobotConfig, RobotMetadata

SETTLE_STEPS = 200
GRIPPER_MIN_STEPS = 120
GRIPPER_MAX_STEPS = 800
TOL = {"revolute": 0.05, "continuous": 0.05, "prismatic": 0.004}  # 追従誤差の許容 [rad / m]


def _check(checks: list, name: str, ok: bool, detail, fix: str | None = None) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail, **({"fix": fix} if fix else {})})
    return ok


def run(project: Project, args, result: SkillResult, app=None) -> None:
    import torch

    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation

    from urdf2vla.sim.lab import T, jacobian_w, write_joint_state
    from urdf2vla.grippers import get_gripper
    from urdf2vla.sim.robot_builder import articulation_cfg, attach_simready_gripper, convert_urdf, disable_mimic_constraints, usd_structure

    cfg = RobotConfig.model_validate(project.read_generated("robot_config.yaml"))
    meta = RobotMetadata.model_validate(project.read_generated("robot_metadata.yaml"))
    jtype = {j.name: j.type for j in meta.joints}

    usd_dir = project.build / "usd"
    usd_path = convert_urdf(cfg, usd_dir, force=bool(getattr(args, "force", False)))
    mimic_disabled = disable_mimic_constraints(usd_path)
    if mimic_disabled:
        print(f"[urdf2vla] disabled mimic constraints (drive all gripper joints instead): {mimic_disabled}")
    # SimReady のグリッパ（URDF のグリッパの代わり）を Robot Assembler で取り付ける
    simready = {g.name: get_gripper(g.simready) for g in cfg.grippers if g.simready}
    for g in cfg.grippers:
        if g.simready and attach_simready_gripper(usd_path, g):
            print(f"[urdf2vla] attached SimReady gripper {g.simready} to {g.mount_link} (Robot Assembler)", flush=True)
    followers = {j for sr in simready.values() for j in sr.followers}  # PhysX の mimic 拘束で動く関節
    structure = usd_structure(usd_path)
    print(f"[urdf2vla] USD: {usd_path}")
    print(f"[urdf2vla] articulation roots: {structure['articulation_roots']}")

    checks: list[dict] = []
    _check(checks, "articulation_root", len(structure["articulation_roots"]) == 1, structure["articulation_roots"])
    _check(checks, "all_joints_driven", not structure["undriven_joints"], {"undriven": structure["undriven_joints"]})
    # URDF の mimic の拘束は無効にして全関節を駆動する（拘束が残っていると gripper の指令と競合する）。
    # SimReady のグリッパの追従関節だけは PhysX の mimic 拘束のまま
    unexpected_mimic = sorted(set(structure["mimic_joints"]) - followers)
    _check(checks, "mimic_constraints_off", not unexpected_mimic, {"active": unexpected_mimic, "disabled": mimic_disabled, "simready_followers": sorted(followers)})

    sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=0.01, device="cuda:0"))
    robot = Articulation(articulation_cfg(cfg, usd_path, "/World/Robot"))
    sim.reset()

    # (a) USD の関節 = URDF の可動関節（mimic の follower も関節として残る）
    expected = set(cfg.joint_limits) | followers  # URDF の可動関節（付け替えたグリッパを除く）+ SimReady のグリッパの関節
    actual = set(robot.joint_names)
    _check(
        checks,
        "joint_name_match",
        expected == actual,
        {"missing_in_usd": sorted(expected - actual), "extra_in_usd": sorted(actual - expected)},
    )
    # (b) IK に使う剛体・カメラの取付先が剛体として存在する
    needed = {a.ik_body for a in cfg.arms}
    if project.has_generated("cameras.yaml"):
        from urdf2vla.schema.camera import CamerasConfig

        needed |= {c.mount_body for c in CamerasConfig.model_validate(project.read_generated("cameras.yaml")).cameras if c.mount_body}
    missing_bodies = sorted(b for b in needed if b not in robot.body_names)
    _check(checks, "bodies_exist", not missing_bodies, {"required": sorted(needed), "missing": missing_bodies})

    # 初期姿勢に置いて保持
    names = robot.joint_names
    q0 = T(robot.data.default_joint_pos).clone()
    write_joint_state(robot, q0)
    dt = sim.get_physics_dt()

    def step(n: int):
        for _ in range(n):
            robot.write_data_to_sim()
            sim.step(render=False)
            robot.update(dt)

    step(SETTLE_STEPS)
    q = T(robot.data.joint_pos)[0]
    # (c) 発散していない
    finite = bool(torch.isfinite(T(robot.data.joint_pos)).all() and torch.isfinite(T(robot.data.body_pos_w)).all())
    _check(checks, "no_nan", finite, "joint_pos / body_pos が有限")
    # (d) 初期姿勢への追従
    errors = {}
    worst = []
    for i, n in enumerate(names):
        if n in cfg.joint_roles.passive:
            continue
        e = abs(float(q[i] - q0[0, i]))
        errors[n] = round(e, 5)
        if e > TOL.get(jtype.get(n, "revolute"), 0.05):
            worst.append(n)
    _check(checks, "default_pose_tracking", finite and not worst, {"max_error": max(errors.values()) if errors else 0.0, "over_tolerance": worst})
    # (e) 関節限界内
    out_of_limits = []
    for i, n in enumerate(names):
        lo, hi = cfg.joint_limits.get(n, [None, None])
        v = float(q[i])
        # 限界ちょうどの目標（グリッパ全開など）では PD の行き過ぎがあるので、少しだけ許容する
        tol = 0.002 if jtype.get(n) == "prismatic" else 0.01
        if (lo is not None and v < lo - tol) or (hi is not None and v > hi + tol):
            out_of_limits.append({"joint": n, "value": round(v, 4), "limits": [lo, hi]})
    _check(checks, "within_limits", not out_of_limits, out_of_limits)

    # End Effector の位置（台・カメラ配置の確認用）
    ee_info = {}
    base = T(robot.data.root_pos_w)[0]
    for arm in cfg.arms:
        idx = robot.body_names.index(arm.ik_body) if arm.ik_body in robot.body_names else None
        if idx is not None:
            p = T(robot.data.body_pos_w)[0, idx] - base
            ee_info[arm.name] = {"ik_body": arm.ik_body, "pos_in_base": [round(float(c), 4) for c in p]}

    # (f) グリッパの開閉（物体なし）: close → open の目標に届くか
    gripper_report = {}
    jvel = {j.name: j.velocity for j in meta.joints}
    for g in cfg.grippers:
        ids = [names.index(j) for j in g.joints]
        # 遅いグリッパ（速度上限が小さい）でも閉じきれるだけの時間をとる
        travel = max(abs(g.open[j] - g.close[j]) / max(jvel.get(j) or 1.0, 1e-3) for j in g.joints)
        gripper_steps = int(min(max(GRIPPER_MIN_STEPS, 1.5 * travel / dt), GRIPPER_MAX_STEPS))
        target = T(robot.data.default_joint_pos).clone()
        for state_name, values in (("close", g.close), ("open", g.open)):
            for j in g.joints:
                target[0, names.index(j)] = values[j]
            robot.set_joint_position_target_index(target=target)
            step(gripper_steps)
            got = {j: round(float(T(robot.data.joint_pos)[0, i]), 4) for j, i in zip(g.joints, ids)}
            err = max(abs(got[j] - values[j]) for j in g.joints)
            gripper_report[f"{g.name}.{state_name}"] = {"target": values, "reached": got, "max_error": round(err, 4)}
        leader_type = jtype.get(g.leader, "revolute")
        bad = [k for k, v in gripper_report.items() if k.startswith(g.name) and v["max_error"] > 2 * TOL.get(leader_type, 0.05)]
        _check(checks, f"gripper_motion:{g.name}", not bad, {k: v["max_error"] for k, v in gripper_report.items() if k.startswith(g.name)})

    # (g) グリッパの可動範囲が効くか: 駆動を切り、関節トルクで限界の外へ押す（開閉の検査だけでは見つからない）
    from urdf2vla.sim.physics_checks import joint_limits_hold

    for chk in joint_limits_hold(sim, robot, cfg, jtype):
        _check(checks, chk["name"], chk["ok"], chk["detail"], chk.get("fix"))
    write_joint_state(robot, T(robot.data.default_joint_pos).clone())
    step(SETTLE_STEPS // 2)

    # 初期姿勢で手先が下を向いているか。向いていなければ IK で推奨姿勢を求めて提案する
    from isaaclab.utils.math import quat_apply

    approach_world = {}
    for arm in cfg.arms:
        bi = robot.body_names.index(arm.ik_body)
        a = quat_apply(T(robot.data.body_quat_w)[0:1, bi], torch.tensor([arm.approach_axis], device=robot.device))[0]
        approach_world[arm.name] = [round(float(c), 3) for c in a]
    # 初期姿勢が特異姿勢に近くないか（近いと teleop や自動操作で IK が止まる）
    sigma_min = {}
    for arm in cfg.arms:
        bi = robot.body_names.index(arm.ik_body)
        jac = jacobian_w(robot, bi, [names.index(j) for j in arm.joints])[0]
        sigma_min[arm.name] = round(float(torch.linalg.svdvals(jac)[-1]), 4)
    near_singular = {k: v for k, v in sigma_min.items() if v < 0.02}
    if near_singular:
        result.warnings.append(
            f"初期姿勢が特異姿勢に近い arm があります（ヤコビアンの最小特異値 {near_singular}）。"
            "そこから動かすと IK が止まることがあります。robot.default_pose を見直してください。"
        )
    not_down = [n for n, a in approach_world.items() if a[2] > -0.7]
    suggestion = None
    answer = project.answers().get("robot.default_pose")
    if not_down and project.has_generated("task.yaml") and answer is None:
        from urdf2vla.schema.task import TaskConfig
        from urdf2vla.sim.pose_suggest import suggest_default_pose

        task = TaskConfig.model_validate(project.read_generated("task.yaml"))
        cx, cy = task.table.center
        targets = {}
        for k, arm in enumerate(cfg.arms):
            if arm.name in task.home_tcp:
                targets[arm.name] = list(task.home_tcp[arm.name])
            else:
                side = 0.0 if len(cfg.arms) == 1 else (0.15 if arm.name == "left" else -0.15 if arm.name == "right" else 0.15 * (1 - 2 * k))
                targets[arm.name] = [cx, cy + side, task.table.top_z + 0.20]
        try:
            suggestion = suggest_default_pose(sim, robot, cfg, targets)
            print(f"[urdf2vla] suggested default pose: {suggestion}")
        except Exception as e:  # noqa: BLE001  提案は補助機能。失敗しても検査結果は返す
            import traceback

            traceback.print_exc()
            result.warnings.append(f"IK による初期姿勢の提案に失敗しました（{type(e).__name__}: {e}）。robot.default_pose を指定してください。")

    report = {
        "usd_path": usd_path,
        "usd": structure,
        "joint_names_sim_order": names,
        "body_names": robot.body_names,
        "checks": checks,
        "tracking_error": errors,
        "end_effectors": ee_info,
        "gripper": gripper_report,
        "approach_axis_world": approach_world,
        "manipulability_sigma_min": sigma_min,
        "mimic_constraints_disabled": mimic_disabled,
        "suggested_default_pose": suggestion,
    }
    (project.build / "import_report.yaml").write_text(dump_yaml(report))
    result.outputs["usd"] = project.rel(usd_path)
    result.outputs["import_report"] = project.rel(project.build / "import_report.yaml")
    result.data = {"checks": [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'}" for c in checks], "end_effectors": ee_info, "joint_order": names}
    for c in checks:
        print(f"[urdf2vla] check {c['name']}: {'OK' if c['ok'] else 'FAIL'} {c['detail']}")
        if not c["ok"]:
            result.fail(f"検査 {c['name']} に失敗しました: {c['detail']}" + (f" → {c['fix']}" if c.get("fix") else ""))
    if suggestion and result.status == "ok":
        from urdf2vla.result import Question

        conv = all(a["converged"] for a in suggestion["arms"].values())
        result.ask(
            Question(
                key="robot.default_pose",
                prompt=(
                    "現在の初期姿勢では手先が下を向いていません（上から掴む Pick & Place に不向き）。"
                    + ("台の上 20cm で手先が真下を向く姿勢を IK で求めました。" if conv else "IK で近い姿勢を求めましたが、完全には一致しませんでした。")
                    + "この推奨姿勢を初期姿勢にしますか？"
                ),
                candidates=["suggested", "keep"],
                reason=f"接近方向（ワールド）: {approach_world}。推奨姿勢の詳細: build/import_report.yaml の suggested_default_pose",
                suggestion=suggestion["joint_pos"],
            )
        )
        result.next = ["configure_robot"]
    elif result.status == "ok":
        result.next = ["launch_sim"]
