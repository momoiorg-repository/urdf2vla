"""launch_sim: Pick & Place 環境を組み立てて起動し、カメラ画像を保存する（Isaac Sim 内）。"""

from __future__ import annotations

import math

from urdf2vla.project import Project, ProjectError, dump_yaml
from urdf2vla.result import SkillResult
from urdf2vla.schema.camera import CamerasConfig

MOTION_AMPLITUDE = 0.25  # 動作確認で arm 関節を振る大きさ [rad]


def _save_png(tensor, path) -> None:
    from PIL import Image

    img = tensor.detach().cpu().numpy()
    if img.shape[-1] == 4:
        img = img[..., :3]
    Image.fromarray(img.astype("uint8")).save(path)


def camera_mount_errors(env, cameras: CamerasConfig) -> dict[str, dict]:
    """link に付けたカメラが設定どおりの位置・向きにあるか（位置の誤差 [m] と向きの誤差 [deg]）。"""
    import torch
    from isaaclab.utils.math import quat_apply, quat_error_magnitude, quat_mul

    from urdf2vla.sim.lab import T

    rob = env.scene["robot"]
    out = {}
    for cam in cameras.cameras:
        if cam.mount != "link":
            continue
        bi = rob.body_names.index(cam.mount_body)
        bp, bq = T(rob.data.body_pos_w)[0:1, bi], T(rob.data.body_quat_w)[0:1, bi]
        exp_p = bp + quat_apply(bq, torch.tensor([cam.pos], device=env.device))
        exp_q = quat_mul(bq, torch.tensor([cam.rot_xyzw], device=env.device))
        d = env.scene[cam.name].data
        out[cam.name] = {
            "position_error_m": round(float(torch.linalg.norm(T(d.pos_w)[0:1] - exp_p)), 4),
            "rotation_error_deg": round(math.degrees(float(quat_error_magnitude(T(d.quat_w_ros)[0:1], exp_q)[0])), 2),
        }
    return out


def run(project: Project, args, result: SkillResult, app=None) -> None:
    import torch

    from urdf2vla.sim.common import load_configs, make_env
    from urdf2vla.sim.lab import T, camera_rgb, env_origin, render_fix

    try:
        c = load_configs(project)
    except ProjectError as e:
        result.fail(str(e))
        return
    robot, cameras, task = c.robot, c.cameras, c.task
    env, env_id = make_env(c, "policy")
    render_fix(env, task)
    print(f"[urdf2vla] env ready: {env_id}, action dim {env.action_manager.total_action_dim}", flush=True)
    from urdf2vla.sim.state import SimView

    view = SimView(env, robot, task, c.vla)

    def place_center(i: int) -> None:
        """物体の候補 i を、物体を置く範囲の中央に置く。"""
        view.place_object(i, view.pose_on_table(i, sum(task.spawn_x) / 2, sum(task.spawn_y) / 2))

    place_center(0)  # 最初の画像に写すのは 1 つ目の物体

    # 行動ベクトル = [arm ごとの関節目標..., gripper ごとの関節目標...]（ActionsCfg の順）
    default = {n: v for n, v in robot.default_pose.items()}
    base_action = []
    layout = []
    for arm in robot.arms:
        layout.append(("arm", arm.name, arm.joints))
        base_action += [default[j] for j in arm.joints]
    for g in robot.grippers:
        layout.append(("gripper", g.name, g.joints))
        base_action.append(1.0)  # 開
    base = torch.tensor(base_action, device=env.device).unsqueeze(0)
    if base.shape[1] != env.action_manager.total_action_dim:
        result.fail(f"行動次元が一致しません: config {base.shape[1]} / env {env.action_manager.total_action_dim}")
        return

    snap_dir = project.snapshots
    snap_dir.mkdir(parents=True, exist_ok=True)
    first: dict[str, torch.Tensor] = {}
    diffs: dict[str, float] = {}
    for cam in cameras.cameras:
        rgb = camera_rgb(env, cam.name)
        first[cam.name] = rgb.clone()
        _save_png(rgb, snap_dir / f"{cam.name}_start.png")

    prev = {name: t.clone() for name, t in first.items()}
    frozen = {name: 0 for name in first}  # 直前の step と完全に同じ画像だった回数（arm は毎 step 動かしている）
    steps = int(getattr(args, "steps", 60) or 60)
    arm_dims = sum(len(a.joints) for a in robot.arms)
    for k in range(steps):
        action = base.clone()
        # arm を小さく振り、後半はグリッパを閉じる（カメラが更新されているか、関節が動くかの確認）
        phase = math.sin(2 * math.pi * k / max(steps, 1))
        action[0, :arm_dims] += MOTION_AMPLITUDE * phase * torch.linspace(1.0, 0.3, arm_dims, device=env.device)
        if k > steps // 2:
            action[0, arm_dims:] = -1.0  # 閉
        env.step(action)
        render_fix(env, task)
        for cam in cameras.cameras:
            rgb = camera_rgb(env, cam.name)
            if torch.equal(rgb, prev[cam.name]):
                frozen[cam.name] += 1
            prev[cam.name] = rgb.clone()
        if k == steps // 4:
            for cam in cameras.cameras:
                rgb = camera_rgb(env, cam.name)
                diffs[cam.name] = float((rgb.float() - first[cam.name].float()).abs().mean())
                _save_png(rgb, snap_dir / f"{cam.name}_moving.png")

    checks = []
    for cam in cameras.cameras:
        rgb = camera_rgb(env, cam.name)
        _save_png(rgb, snap_dir / f"{cam.name}_end.png")
        mean = float(rgb.float().mean())
        alive = diffs.get(cam.name, 0.0) > 0.5 and frozen[cam.name] == 0
        checks.append(
            {
                "name": f"camera_alive:{cam.name}",
                "ok": alive,
                "detail": {"mean_abs_diff": round(diffs.get(cam.name, 0.0), 3), "frozen_steps": frozen[cam.name], "steps": steps},
            }
        )
        checks.append({"name": f"camera_not_black:{cam.name}", "ok": mean > 5.0, "detail": {"mean_intensity": round(mean, 2)}})

    from isaaclab.utils.math import quat_apply

    cam_poses = {}
    for cam in cameras.cameras:
        data = env.scene[cam.name].data
        pos = T(data.pos_w)[0] - env_origin(env)
        fwd = quat_apply(T(data.quat_w_ros)[0:1], torch.tensor([[0.0, 0.0, 1.0]], device=env.device))[0]
        down = quat_apply(T(data.quat_w_ros)[0:1], torch.tensor([[0.0, 1.0, 0.0]], device=env.device))[0]
        cam_poses[cam.name] = {
            "pos": [round(float(c), 4) for c in pos],
            "forward": [round(float(c), 3) for c in fwd],
            "image_down": [round(float(c), 3) for c in down],
        }
        if cam.mount == "link":
            # 設定どおりに付いているか: 取付剛体の姿勢 × オフセット から期待される向きと比べる
            from isaaclab.utils.math import quat_mul

            rob = env.scene["robot"]
            bi = rob.body_names.index(cam.mount_body)
            q_body = T(rob.data.body_quat_w)[0:1, bi]
            q_off = torch.tensor([cam.rot_xyzw], device=env.device)
            exp_fwd = quat_apply(quat_mul(q_body, q_off), torch.tensor([[0.0, 0.0, 1.0]], device=env.device))[0]
            exp_pos = T(rob.data.body_pos_w)[0, bi] + quat_apply(q_body, torch.tensor([cam.pos], device=env.device))[0] - env_origin(env)
            cam_poses[cam.name]["expected_forward"] = [round(float(c), 3) for c in exp_fwd]
            cam_poses[cam.name]["expected_pos"] = [round(float(c), 4) for c in exp_pos]
            cam_poses[cam.name]["body_quat_w"] = [round(float(c), 4) for c in q_body[0]]

    # 上から掴む Pick & Place では、初期姿勢で手先の接近方向が下を向いている方が扱いやすい
    approach_world = {}
    rob = env.scene["robot"]
    for arm in robot.arms:
        bi = rob.body_names.index(arm.ik_body)
        a = quat_apply(T(rob.data.body_quat_w)[0:1, bi], torch.tensor([arm.approach_axis], device=env.device))[0]
        approach_world[arm.name] = [round(float(c), 3) for c in a]
        if float(a[2]) > -0.7:
            result.warnings.append(
                f"arm '{arm.name}' の初期姿勢では手先が下を向いていません（接近方向 {approach_world[arm.name]}）。"
                "上から掴む作業なら robot.default_pose を手先が真下を向く姿勢にしてください。"
            )

    # 手先カメラが設定どおりの位置・向きで link に付いているか（腕を動かした後で確認）
    mount = camera_mount_errors(env, cameras)
    for name, err in mount.items():
        ok = err["position_error_m"] < 0.01 and err["rotation_error_deg"] < 3.0
        checks.append({"name": f"camera_mount:{name}", "ok": ok, "detail": err})

    joint_pos = T(env.scene["robot"].data.joint_pos)[0]
    finite = bool(torch.isfinite(joint_pos).all())
    checks.append({"name": "no_nan", "ok": finite, "detail": "関節角が有限"})
    # 物体の候補ごと: 台に置いて落ち着くか（台を突き抜けない・跳ねない）、生成された大きさがカタログと合うか。
    # 丸い物体は直立では倒れて転がる（SimReady のりんごは 2 秒ほどで倒れて 10 cm 転がった）ので、しばらく置いて
    # 落ち着いた向きと高さを測り（build/object_rest.json）、エピソードではその向きで置く
    import json

    from urdf2vla.sim.physics_checks import asset_size_check

    multi = len(task.objects) > 1
    rest = {}
    for i, o in enumerate(task.objects):
        place_center(i)
        start = view.object_center().clone()
        for _ in range(25 if o.box else 150):
            env.step(base)
        center = view.object_center()
        root_p, root_q = view.object_pose()
        lo, hi = task.table.top_z + min(o.size) / 2 - 0.01, task.table.top_z + max(o.size) / 2 + 0.01
        moved = float(torch.linalg.norm(center[:2] - start[:2]))
        on_table = lo <= float(center[2]) <= hi
        name = f"object_on_table:{o.key}" if multi else "object_on_table"
        checks.append({"name": name, "ok": on_table, "detail": {"object_center": [round(float(v), 4) for v in center], "expected_z": [round(lo, 4), round(hi, 4)], "moved_while_settling_m": round(moved, 4)}})
        rest[o.key] = {"quat_xyzw": [round(float(v), 6) for v in root_q], "root_height": round(float(root_p[2]) - task.table.top_z, 5)}
        if o.asset is not None:
            checks.append(asset_size_check(env, f"object_{i}", o.key, o.size))
        env.reset()
    (project.build / "object_rest.json").write_text(json.dumps(rest, indent=1))
    view.set_rest_poses(rest)
    if task.target.is_container:
        checks.append(asset_size_check(env, "target", task.target.key, task.target.size))

    # 掴めるか: 指の間に物体を置いて閉じ、離しても落ちないか・指が物体にめり込んでいないか（最後に行う。環境を reset する）
    from urdf2vla.sim.physics_checks import grasp_hold

    jtype = {j.name: j.type for j in c.meta.joints} if c.meta else {}
    grasp_report = {}
    for i, o in enumerate(task.objects):
        grasp_checks, rep = grasp_hold(env, view, c, base, jtype, index=i)
        checks += grasp_checks
        grasp_report[o.key] = rep

    report = {
        "env_id": env_id,
        "action_dim": env.action_manager.total_action_dim,
        "action_layout": [{"kind": k, "name": n, "joints": j} for k, n, j in layout],
        "control_hz": task.control_hz,
        "cameras": {c.name: {"resolution": [c.width, c.height], "mount": c.mount_body or "world"} for c in cameras.cameras},
        "camera_poses_world": cam_poses,
        "approach_axis_world": approach_world,
        "checks": checks,
        "grasp": grasp_report,
        # 物体ごとに測った剛体の原点から外接箱の中心・底へのオフセットと、生成時の剛体の向き（直立）
        "object_frames": {
            o.key: {"center_offset": [round(float(v), 4) for v in view.center_off0[i]], "bottom": round(view.bottom0[i], 4),
                    "spawn_quat_xyzw": [round(float(v), 4) for v in view.quat0[i]]}
            for i, o in enumerate(task.objects)
        },  # fmt: skip
        "snapshots": sorted(p.name for p in snap_dir.glob("*.png")),
    }
    (project.build / "launch_report.yaml").write_text(dump_yaml(report))
    result.outputs["launch_report"] = project.rel(project.build / "launch_report.yaml")
    result.outputs["snapshots"] = project.rel(snap_dir)
    result.data = {"env_id": env_id, "camera_poses_world": cam_poses, "checks": [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'}" for c in checks], "snapshots": report["snapshots"]}
    for c in checks:
        print(f"[urdf2vla] check {c['name']}: {'OK' if c['ok'] else 'FAIL'} {c['detail']}", flush=True)
        if not c["ok"]:
            result.fail(f"検査 {c['name']} に失敗しました: {c['detail']}" + (f" → {c['fix']}" if c.get("fix") else ""))

    if getattr(args, "livestream", False) and app is not None:
        print("[urdf2vla] livestream: WebRTC クライアントで接続してください（Ctrl-C で終了）", flush=True)
        project.record(result)
        while app.is_running():
            env.step(base)
    # env.close() / app.close() は終了処理で止まることがあるため呼ばない（entry が os._exit する）
    if result.status == "ok":
        result.next = ["configure_vla_interface"]
