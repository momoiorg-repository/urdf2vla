"""初期姿勢の提案: 台の上で TCP が真下を向く姿勢を IK（減衰最小二乗）で探す（Isaac Sim 内）。

ユーザが関節角を知らなくても、上から掴む作業を始めやすい姿勢を提示する。
採用するかどうかはユーザが決める（import_robot が質問として返す）。
"""

from __future__ import annotations

import torch

from urdf2vla.schema.robot import ArmConfig, RobotConfig

ITERS = 200
POS_TOL = 0.01  # m
SIGMA_MIN = 0.05  # ヤコビアンの最小特異値の下限（これより小さい姿勢は特異姿勢に近い）
ROT_TOL = 0.05  # rad


def _target_rotation(arm: ArmConfig, yaw_90: bool, device) -> torch.Tensor:
    """ik_body の接近方向をワールド -Z に、指の開閉方向をワールド Y（or X）に合わせる回転行列。"""
    a = torch.tensor(arm.approach_axis, dtype=torch.float32, device=device)
    f = torch.tensor(arm.finger_axis or [0.0, 1.0, 0.0], dtype=torch.float32, device=device)
    f = f - (f @ a) * a
    if f.norm() < 1e-6:
        f = torch.tensor([1.0, 0.0, 0.0], device=device) if abs(float(a[0])) < 0.9 else torch.tensor([0.0, 1.0, 0.0], device=device)
        f = f - (f @ a) * a
    f = f / f.norm()
    body = torch.stack([a, f, torch.linalg.cross(a, f)], dim=1)
    wa = torch.tensor([0.0, 0.0, -1.0], device=device)
    wf = torch.tensor([1.0, 0.0, 0.0] if yaw_90 else [0.0, 1.0, 0.0], device=device)
    world = torch.stack([wa, wf, torch.linalg.cross(wa, wf)], dim=1)
    return world @ body.T


def suggest_default_pose(sim, robot, cfg: RobotConfig, tcp_targets: dict[str, list[float]], seed_count: int = 6) -> dict:
    """arm ごとに TCP を tcp_targets[arm]（ワールド座標）へ真下向きで置く関節角を探す。"""
    from isaaclab.controllers import DifferentialIKController, DifferentialIKControllerCfg
    from isaaclab.utils.math import quat_error_magnitude, quat_from_matrix

    from urdf2vla.sim.lab import T, jacobian_w, write_joint_state

    device = robot.device
    names = robot.joint_names
    dt = sim.get_physics_dt()
    q_full = T(robot.data.default_joint_pos).clone()
    limits = T(robot.data.soft_joint_pos_limits)
    lo = limits[0, :, 0].clone()
    hi = limits[0, :, 1].clone()
    # 可動範囲のない関節（continuous）は ±inf になるので、探索は [-π, π] に限る
    finite = torch.isfinite(lo) & torch.isfinite(hi) & ((hi - lo) < 4 * torch.pi)
    lo = torch.where(finite, lo, torch.full_like(lo, -torch.pi))
    hi = torch.where(finite, hi, torch.full_like(hi, torch.pi))
    gen = torch.Generator(device="cpu").manual_seed(0)
    result = {"joint_pos": {}, "arms": {}}

    for arm in cfg.arms:
        ids = [names.index(j) for j in arm.joints]
        body = robot.body_names.index(arm.ik_body)
        tcp = torch.tensor([arm.tcp_offset.pos], device=device)
        ik = DifferentialIKController(
            DifferentialIKControllerCfg(command_type="pose", use_relative_mode=False, ik_method="dls", ik_params={"lambda_val": 0.05}),
            num_envs=1,
            device=device,
        )
        target_tcp = torch.tensor([tcp_targets[arm.name]], device=device) + T(robot.data.root_pos_w)[0:1]

        seeds = [q_full[0, ids].clone(), torch.zeros(len(ids), device=device).clamp(lo[ids], hi[ids])]
        for _ in range(seed_count):
            r = torch.rand(len(ids), generator=gen).to(device)
            seeds.append(lo[ids] + r * (hi[ids] - lo[ids]))

        best = None
        for yaw_90 in (False, True):
            rot = _target_rotation(arm, yaw_90, device)
            q_des = quat_from_matrix(rot.unsqueeze(0))
            p_body_des = target_tcp - (rot @ tcp[0]).unsqueeze(0)
            ik.set_command(torch.cat([p_body_des, q_des], dim=1))
            for seed in seeds:
                q = q_full.clone()
                q[0, ids] = seed
                for _ in range(ITERS):
                    write_joint_state(robot, q)
                    robot.write_data_to_sim()
                    sim.step(render=False)
                    robot.update(dt)
                    jac = jacobian_w(robot, body, ids)
                    pos = T(robot.data.body_pos_w)[:, body]
                    quat = T(robot.data.body_quat_w)[:, body]
                    q_arm = ik.compute(pos, quat, jac, q[:, ids])
                    q_arm = torch.nan_to_num(q_arm[0], nan=0.0)
                    q[0, ids] = torch.max(torch.min(q_arm, hi[ids] + 2 * torch.pi * (~finite[ids])), lo[ids] - 2 * torch.pi * (~finite[ids]))
                pos = T(robot.data.body_pos_w)[:, body]
                quat = T(robot.data.body_quat_w)[:, body]
                pos_err = float((pos - p_body_des).norm())
                rot_err = float(quat_error_magnitude(quat, q_des))
                # 関節限界からの余裕（大きいほど動かしやすい）
                lim = finite[ids]
                if bool(lim.any()):
                    m = torch.min(q[0, ids] - lo[ids], hi[ids] - q[0, ids]) / (hi[ids] - lo[ids] + 1e-6)
                    margin = float(m[lim].min())
                else:
                    margin = 0.5
                # 範囲のない関節は初期値（seed）から大きく回り込んでいない方がよい（実機で巻き付かない）
                margin -= 0.05 * float((q[0, ids][~lim]).abs().sum()) if bool((~lim).any()) else 0.0
                ok = pos_err < POS_TOL and rot_err < ROT_TOL
                # 特異姿勢から遠いか（ヤコビアンの最小特異値）。近いと、そこから少し動かそうとしただけで IK が止まる
                sigma = float(torch.linalg.svdvals(jac[0])[-1])
                well = sigma >= SIGMA_MIN
                score = (0 if ok else 1, 0 if well else 1, -(margin + 2.0 * min(sigma, 0.2)) if ok else pos_err + rot_err)
                if best is None or score < best[0]:
                    best = (score, q[0, ids].clone(), pos_err, rot_err, ok, margin, sigma)
        _, q_best, pos_err, rot_err, ok, margin, sigma = best
        q_full[0, ids] = q_best
        for j, i in zip(arm.joints, ids):
            result["joint_pos"][j] = round(float(q_best[ids.index(i)]), 4)
        result["arms"][arm.name] = {
            "converged": ok,
            "tcp_target": tcp_targets[arm.name],
            "position_error": round(pos_err, 4),
            "orientation_error": round(rot_err, 4),
            "limit_margin": round(margin, 3),
            "manipulability_sigma_min": round(sigma, 4),
        }
    # arm 以外の関節は現在の初期姿勢のまま
    for j, v in cfg.default_pose.items():
        result["joint_pos"].setdefault(j, v)
    # 元の初期姿勢に戻しておく
    write_joint_state(robot, T(robot.data.default_joint_pos).clone())
    return result
