"""グリッパの物理の検査（Isaac Sim 内）。「動くが掴めない」グリッパを、データを集める前に見つける。

このプロジェクトで見つかった不具合はどれも、開閉の検査（関節が目標に届くか）を通っていた:
- URDF から変換した簡略版 Robotiq（CRX-3iA）: 左 knuckle の可動範囲が Isaac Sim 6.2 で効かず、物を挟むと押し戻された
  （関節トルクで押すと押した向きと逆に回り、範囲 0〜0.8 rad を越えて -1.0 / +2.3 rad まで行く）
  → joint_limits_hold（駆動を切って関節トルクで押し、可動範囲の中に留まるか）
- Franka（ソルバーの反復 8 回）: 45mm の立方体を挟むと、指が 23mm まで閉じた（めり込んだ）
  → grasp_hold（指の間に立方体を置いて閉じ、離しても落ちないか。直動の指なら指の開きが立方体の幅と合うか）
"""

from __future__ import annotations

import torch

from urdf2vla.sim.lab import T, write_root_pose

LIMIT_TOL = {"prismatic": 0.003, "revolute": 0.05, "continuous": 0.05}  # 可動範囲を越えてよい量 [m / rad]
PUSH_STEPS = 300
OPENING_TOL = 0.004  # 直動の指の開きが物体の幅より狭くてよい量 [m]（接触のめり込み）
HELD_TOL = 0.02  # 離した後に物体が TCP から離れてよい距離 [m]

# 失敗したときに、直し方として示す文（project.yaml のキー）
FIX = {
    "joint_limits_hold": "グリッパの関節の可動範囲がシミュレーションで効いていません。URDF から変換したグリッパなら、"
    "SimReady のグリッパに付け替えてください（project.yaml の robot.gripper.simready、例: robotiq_2f_85。"
    "configure_robot からやり直す）。",
    "grasp_hold": "物体を挟んでも保持できません。project.yaml の overrides.robot.asset.solver_position_iterations を増やして"
    "（既定 32、SimReady は 64）import_robot からやり直してください。",
    "grasp_opening": "指が物体にめり込んでいます。project.yaml の overrides.robot.asset.solver_position_iterations を増やして"
    "（既定 32、SimReady は 64）import_robot からやり直してください。",
}


def joint_limits_hold(sim, robot, cfg, jtype: dict[str, str]) -> list[dict]:
    """グリッパの駆動を切り、関節トルク（上限の半分）で両側の限界の外へ押して、可動範囲で止まるかを調べる。"""
    names = robot.joint_names
    dt = sim.get_physics_dt()
    lim = T(robot.data.joint_pos_limits)[0]
    effort = T(robot.data.joint_effort_limits)[0]
    checks = []
    for g in cfg.grippers:
        ids = [names.index(j) for j in g.joints]
        stiff0 = T(robot.data.joint_stiffness)[0:1, ids].clone()
        damp0 = T(robot.data.joint_damping)[0:1, ids].clone()
        robot.write_joint_stiffness_to_sim_index(stiffness=torch.zeros_like(stiff0), joint_ids=ids)
        robot.write_joint_damping_to_sim_index(damping=damp0 * 0.1, joint_ids=ids)
        detail, bad = {}, []
        for sign, side in ((-1.0, "lower"), (1.0, "upper")):
            tau = torch.zeros_like(T(robot.data.joint_pos))
            for i in ids:
                tau[0, i] = sign * 0.5 * float(effort[i])
            for _ in range(PUSH_STEPS):
                robot.set_joint_effort_target_index(target=tau)
                robot.write_data_to_sim()
                sim.step(render=False)
                robot.update(dt)
            q = T(robot.data.joint_pos)[0]
            for j, i in zip(g.joints, ids):
                lo, hi = float(lim[i, 0]), float(lim[i, 1])
                v = float(q[i])
                # 押した側に限らず、可動範囲の外にいれば失敗（URDF の簡略版 Robotiq の左 knuckle は、押した向きと逆に回って範囲を越えた）
                over = max(lo - v, v - hi, 0.0)
                detail[f"{j}.push_{side}"] = {"value": round(v, 4), "range": [round(lo, 4), round(hi, 4)], "over": round(over, 4)}
                if over > LIMIT_TOL.get(jtype.get(j, "revolute"), 0.05):
                    bad.append(f"{j}.push_{side}")
        robot.set_joint_effort_target_index(target=torch.zeros_like(T(robot.data.joint_pos)))
        robot.write_joint_stiffness_to_sim_index(stiffness=stiff0, joint_ids=ids)
        robot.write_joint_damping_to_sim_index(damping=damp0, joint_ids=ids)
        checks.append({"name": f"joint_limits_hold:{g.name}", "ok": not bad, "detail": {"over_limit": bad, "joints": detail}, "fix": FIX["joint_limits_hold"]})
    return checks


def grasp_hold(env, view, c, base_action: torch.Tensor, jtype: dict[str, str], index: int = 0) -> tuple[list[dict], dict]:
    """指の間（TCP）に物体の候補 index を置いて閉じ、物体を離しても落ちないか、直動の指なら指の開きが物体の幅と合うかを調べる。

    物体は外接箱の中心を TCP に、x 軸（短い辺の側）を指の開閉方向に向けて置き、指が閉じ終わるまでその場に保持してから離す。
    腕は初期姿勢のまま。物体の候補が複数あれば、検査の名前に物体の key を付ける。
    """
    from isaaclab.utils.math import quat_apply, quat_from_matrix

    checks, report = [], {}
    robot_cfg = c.robot
    arm = robot_cfg.arms[0]
    g = robot_cfg.gripper_for(arm.name)
    if g is None:
        return checks, report
    view.active = index
    ocfg = view.object_cfg
    suffix = f":{ocfg.key}" if len(c.task.objects) > 1 else ""
    width = float(ocfg.size[0])
    n_arm = sum(len(a.joints) for a in robot_cfg.arms)
    open_a, close_a = base_action.clone(), base_action.clone()
    open_a[0, n_arm:] = 1.0
    close_a[0, n_arm:] = -1.0
    env.reset()
    for _ in range(20):
        env.step(open_a)
    # 物体の向き: x = 指の開閉方向、z = 接近方向（どちらも world）
    body_q = T(view.robot.data.body_quat_w)[0:1, view.body_ids[arm.name]]
    a_w = quat_apply(body_q, torch.tensor([arm.approach_axis], device=env.device, dtype=torch.float32))[0]
    f_body = arm.finger_axis or [1.0, 0.0, 0.0]
    f_w = quat_apply(body_q, torch.tensor([f_body], device=env.device, dtype=torch.float32))[0]
    a_w = a_w / torch.linalg.norm(a_w)
    f_w = f_w - torch.dot(f_w, a_w) * a_w
    f_w = f_w / torch.linalg.norm(f_w)
    rot = torch.stack([f_w, torch.linalg.cross(a_w, f_w), a_w], dim=1)
    q_dir = quat_from_matrix(rot.unsqueeze(0))[0]
    # 物体の直立の向き（生成時）の x を指の開閉方向、z を接近方向に合わせる（剛体の向きは q_dir × 生成時の向き）
    from isaaclab.utils.math import quat_mul

    q_obj = quat_mul(q_dir.unsqueeze(0), view.quat0[index].unsqueeze(0))[0]
    tcp, _ = view.tcp_pose(arm.name)
    # 外接箱の中心を TCP に（剛体の原点はアセットによって底や別の点にある）
    pose = torch.cat([tcp - view.center_offset(index, q_obj) + view.origin, q_obj]).unsqueeze(0)
    for k in range(60):
        if k < 20:  # 指が閉じ終わるまで物体をその場に保持し、その後は指だけで支える
            write_root_pose(view.object, pose)
        env.step(close_a)
    obj_p = view.object_center()
    tcp_now, _ = view.tcp_pose(arm.name)
    drift = float(torch.linalg.norm(obj_p - tcp_now))
    report["object_offset_from_tcp_m"] = round(drift, 4)
    checks.append(
        {
            "name": f"grasp_hold:{g.name}{suffix}",
            "ok": drift < HELD_TOL,
            "detail": {"object_offset_from_tcp_m": round(drift, 4), "tolerance_m": HELD_TOL},
            "fix": FIX["grasp_hold"],
        }
    )
    # 直動の指: 指の開き（閉じた位置からの移動量の和）が物体の幅と合うか（めり込みの検出）
    if ocfg.box and all(jtype.get(j) == "prismatic" for j in g.joints):
        names = view.robot.joint_names
        q = T(view.robot.data.joint_pos)[0]
        opening = sum(abs(float(q[names.index(j)]) - g.close[j]) for j in g.joints)
        report["finger_opening_m"] = round(opening, 4)
        report["object_width_m"] = width
        checks.append(
            {
                "name": f"grasp_opening:{g.name}{suffix}",
                "ok": opening >= width - OPENING_TOL,
                "detail": {"finger_opening_m": round(opening, 4), "object_width_m": width, "tolerance_m": OPENING_TOL},
                "fix": FIX["grasp_opening"],
            }
        )
    env.reset()
    return checks, report


SIZE_TOL = 0.15  # 生成された物体の外接箱がカタログの値からずれてよい割合


def asset_size_check(env, entity: str, key: str, expected: list[float]) -> dict:
    """場面に生成した物体（env 0）の外接箱を USD から測り、カタログの大きさ（縮尺後）と比べる。

    外れていれば、縮尺の誤り・アセットの単位（cm / m）の違い・variant の選び間違いを疑う。
    """
    from pxr import Usd, UsdGeom

    import isaaclab.sim as sim_utils

    asset = env.scene[entity]
    prim = sim_utils.find_matching_prims(asset.cfg.prim_path)[0]
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"], useExtentsHint=False)
    box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    measured = [round(float(v), 4) for v in (box.GetMax() - box.GetMin())]
    # 水平の 2 辺は向きで入れ替わるので、並べ替えて比べる
    pairs = list(zip(sorted(measured[:2]) + [measured[2]], sorted(expected[:2]) + [expected[2]]))
    ok = all(abs(m - e) <= SIZE_TOL * e + 0.003 for m, e in pairs)
    return {"name": f"asset_size:{key}", "ok": ok, "detail": {"measured_m": measured, "catalog_m": [round(v, 4) for v in expected]}}
