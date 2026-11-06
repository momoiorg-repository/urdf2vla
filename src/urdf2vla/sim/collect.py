"""collect_dataset: teleop でデモを記録して HDF5 に保存する（Isaac Sim 内）。

HDF5 の構成（Isaac Lab の /data/demo_N 形式に合わせ、VLA 変換に必要な情報を属性に持たせる）:
  /data                attrs: fps, state_names, action_names, cameras, task, vla_interface, robot_name, env_id, render_fix, ...
  /data/demo_N         attrs: success, num_samples, device, seed
    obs/state          (T, state_dim)  float32   VLA の state（arm 関節角 + gripper スカラー）
    obs/images/<cam>   (T, H, W, 3)    uint8
    obs/tcp_pos        (T, 3*arms)     float32   base 座標
    obs/tcp_quat       (T, 4*arms)     float32   (x, y, z, w)
    obs/object_pos     (T, 3)          float32   env 座標
    obs/object_quat    (T, 4)          float32   (x, y, z, w)
    actions            (T, action_dim) float32   行動ラベル = IK が出した関節目標 + gripper スカラー
    teleop_actions     (T, 7*arms + grippers)    teleop 環境に入れた行動（目標 TCP 姿勢 + ±1）
    initial_state      joint_pos (n_joints), object_pose (7: 位置 + (x, y, z, w)), object_index (1),
                       object_place_pose (7: 置いた姿勢。落ち着かせる前)  再生用
  attrs: object（task.objects の key）, language_instruction（このエピソードの指示文）

urdf2vla_format 2 = Isaac Lab 3.0 版（四元数は (x, y, z, w)）。1 は Isaac Lab 2.x 版（(w, x, y, z)）で、再生できない。
"""

from __future__ import annotations

import json
import time

import torch

from urdf2vla.project import Project, dump_yaml
from urdf2vla.result import SkillResult

FROZEN_TOLERANCE = 0  # 腕が動いているのに画像が完全に同じだった step 数の許容
REPLAY_TOLERANCE = 0.02  # ラベル再生時の arm 関節の追従誤差の許容 [rad]
DATASET_FORMAT = 2  # HDF5 の urdf2vla_format（上の説明）
STALE_FIRST_FRAME_RATIO = 4.0  # 最初の 2 フレームの差が、続くフレーム間の差の何倍を超えたら「reset 前の画像」とみなすか


def _first_frame_jump(frames: list) -> float:
    """最初の 2 フレームの差 / 続くフレーム間の差の中央値。reset 直後の画像が古い（reset 前の場面）と大きくなる。"""
    import numpy as np

    if len(frames) < 6:
        return 0.0
    d = [float(np.abs(frames[i].astype(np.float32) - frames[i + 1].astype(np.float32)).mean()) for i in range(5)]
    return d[0] / max(float(np.median(d[1:])), 0.05)


class EpisodeBuffer:
    def __init__(self):
        self.data: dict[str, list] = {}

    def add(self, key: str, value) -> None:
        self.data.setdefault(key, []).append(value)

    def __len__(self) -> int:
        return len(self.data.get("actions", []))


def write_episode(h5, idx: int, buf: EpisodeBuffer, initial: dict, attrs: dict) -> None:
    import numpy as np

    g = h5["data"].create_group(f"demo_{idx}")
    for k, v in attrs.items():
        g.attrs[k] = v
    g.attrs["num_samples"] = len(buf)
    for key, seq in buf.data.items():
        arr = np.stack(seq)
        kw = {}
        if key.startswith("obs/images/"):
            kw = {"compression": "gzip", "compression_opts": 4, "chunks": (1, *arr.shape[1:])}
        g.create_dataset(key, data=arr, **kw)
    for key, value in initial.items():
        g.create_dataset(f"initial_state/{key}", data=np.asarray(value, dtype=np.float32))


def _save_failure_frames(project: Project, buf: EpisodeBuffer, cams: list[str]) -> None:
    """最初に失敗した試行の画像を何枚か保存する（何が起きたかを目で確認するため）。"""
    from PIL import Image

    n = len(buf)
    if n == 0:
        return
    project.snapshots.mkdir(parents=True, exist_ok=True)
    for cam in cams:
        frames = buf.data.get(f"obs/images/{cam}", [])
        for frac in (0.2, 0.35, 0.5, 0.7):
            i = min(int(frac * n), n - 1)
            Image.fromarray(frames[i]).save(project.snapshots / f"collect_fail_{cam}_{int(frac * 100):02d}.png")


def _replay_labels(env, view, c, demo: dict) -> dict:
    """記録した行動ラベル（関節目標 + gripper スカラー）だけで、評価環境と同じ方法で腕を動かしてタスクが成功するか。"""
    from urdf2vla.sim.lab import T, render_fix, write_joint_state

    robot = view.robot
    names = robot.joint_names
    env.reset()
    q0 = torch.tensor(demo["joint_pos0"], device=env.device).unsqueeze(0)
    write_joint_state(robot, q0)
    view.place_object(int(demo["object_index"]), list(demo["object_place_pose"]))

    arm_idx = [names.index(j) for g in c.vla.action.groups if g.kind == "joint_position" for j in g.joints]
    grippers = [view.grippers[g.gripper] for g in c.vla.action.groups if g.kind == "gripper_scalar"]
    n_arm = len(arm_idx)
    success_steps = 0
    track_err = 0.0
    free_err = 0.0  # 最初にグリッパを閉じるまで（何にも触れていない区間）
    in_contact = False
    states = demo["states"]
    for t, a in enumerate(demo["actions"]):
        target = T(robot.data.joint_pos_target).clone()
        target[0, arm_idx] = torch.tensor(a[:n_arm], device=env.device)
        for gi, g in enumerate(grippers):
            view.set_gripper_targets(target, g.name, a[n_arm + gi] >= c.vla.action.gripper_threshold)
        robot.set_joint_position_target_index(target=target)
        for _ in range(c.task.decimation):
            env.scene.write_data_to_sim()
            env.sim.step(render=False)
            env.scene.update(env.physics_dt)
        render_fix(env, c.task)
        open_now = all(view.gripper_scalar(g.name, "target") >= 0.5 for g in grippers)
        success_steps = success_steps + 1 if view.task_success(open_now) else 0
        # 記録時の次の状態（arm 関節）と比べる: ラベルが正しければ同じ軌道をたどる
        if any(a[n_arm + gi] < c.vla.action.gripper_threshold for gi in range(len(grippers))):
            in_contact = True
        if t + 1 < len(states):
            q = T(robot.data.joint_pos)[0, arm_idx]
            ref = torch.tensor(states[t + 1][:n_arm], device=env.device)
            err = float((q - ref).abs().max())
            track_err = max(track_err, err)
            if not in_contact:
                free_err = max(free_err, err)
    pos, _ = view.object_pose()
    return {
        "arm_tracking_max_error_rad": round(track_err, 4),
        "free_motion_max_error_rad": round(free_err, 4),
        "task_success": success_steps > 0,
        "final_object_pos": [round(float(x), 4) for x in pos],
    }


def run(project: Project, args, result: SkillResult, app=None) -> None:
    import h5py

    from urdf2vla.sim.common import load_configs, make_env
    from urdf2vla.sim.lab import render_fix
    from urdf2vla.sim.state import SimView
    from urdf2vla.sim.teleop import TargetIntegrator, make_device

    c = load_configs(project)
    if c.vla is None or c.teleop is None:
        result.fail("vla_interface.yaml / teleop.yaml がありません。configure_vla_interface と setup_teleoperation を先に実行してください。")
        return
    device_kind = args.device or c.teleop.device
    num_demos = int(args.num_demos)
    max_attempts = int(args.max_attempts) or num_demos * 3
    env, env_id = make_env(c, "teleop")
    torch.manual_seed(int(args.seed))  # 環境の作成（Isaac Lab）が乱数の種を設定し直すので、その後で設定する
    view = SimView(env, c.robot, c.task, c.vla, project_root=project.root)
    source = str(project.root / args.source) if getattr(args, "source", None) else None
    device = make_device(device_kind, c.teleop, view, source)
    integ = TargetIntegrator(c.teleop, view)
    cams = [i.camera for i in c.vla.images]
    max_step = c.teleop.scripted_max_step if device_kind == "scripted" else c.teleop.pos_step

    out_dir = project.root / "datasets"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.dataset}.hdf5"
    h5 = h5py.File(path, "a")
    data = h5.require_group("data")
    start_idx = len([k for k in data.keys() if k.startswith("demo_")])
    data.attrs.update(
        {
            "urdf2vla_format": DATASET_FORMAT,
            "robot_name": c.robot.robot_name,
            "env_id": env_id,
            "fps": c.vla.control_hz,
            "render_fix": c.task.render_fix,
            "state_names": json.dumps(c.vla.state.names),
            "action_names": json.dumps(c.vla.action.names),
            "joint_names_sim_order": json.dumps(view.robot.joint_names),
            "cameras": json.dumps({cam.name: cam.model_dump(mode="json") for cam in c.cameras.cameras}),
            "task": json.dumps(c.task.model_dump(mode="json")),
            "vla_interface": json.dumps(c.vla.model_dump(mode="json")),
            "language_instruction": c.vla.language_instruction,
        }
    )
    print(f"[urdf2vla] recording to {path} with device={device_kind}", flush=True)
    if device_kind != "scripted":
        print("[urdf2vla] キー: W/S A/D Q/E 移動, Z/X T/G C/V 回転, K グリッパ, N arm 切替, R やり直し, ESC 終了", flush=True)

    saved, attempts = 0, 0
    episodes = []
    frozen_total = 0
    stale_first = 0.0
    first_demo = None
    t_start = time.time()
    quit_all = False
    saved_failure_frames = False
    while saved < num_demos and attempts < max_attempts and not quit_all:
        attempts += 1
        env.reset()
        if device_kind == "mimic":  # Mimic で生成したエピソード: 生成時の初期状態から始める（物体は 1 つ）
            if not device.start(env):
                break
            view.active = 0
            op, oq = view.object_pose()
            obj_index, place_pose = 0, op.tolist() + oq.tolist()
        else:
            obj_index, place_pose = view.start_episode()  # 物体の候補から 1 つを選んで台に置く
        render_fix(env, c.task)
        device.reset(view)
        integ.reset(view)
        buf = EpisodeBuffer()
        obj_p, obj_q = view.object_pose()
        initial = {
            "joint_pos": view.joint_pos().tolist(),
            "object_pose": obj_p.tolist() + obj_q.tolist(),
            "object_index": [obj_index],
            "object_place_pose": place_pose,  # 置いた姿勢（落ち着かせる前）。再生はここから同じ手順で置く
        }
        tcp_start = {a.name: view.tcp_pose_root(a.name)[0].clone() for a in c.robot.arms}
        prev_img = None
        prev_q = None
        frozen = 0
        held = 0
        obj_max_z = float(obj_p[2])
        outcome = "timeout"
        step_start = time.time()
        for step in range(c.teleop.max_episode_steps):
            if device_kind in ("keyboard", "socket"):  # 人が操作するときは実時間より速く進めない（1 制御周期 = 1/control_hz 秒）
                wait = step_start + 1.0 / c.task.control_hz - time.time() if step else 0.0
                if wait > 0:
                    time.sleep(wait)
                step_start = time.time()
            cmd = device.advance(view, integ)
            if cmd.quit:
                quit_all, outcome = True, "quit"
                break
            if cmd.reset:
                outcome = "reset"
                break
            # 観測（step 前）
            imgs = view.images(cams)
            q_now = view.joint_pos().clone()
            if prev_img is not None and float((q_now - prev_q).abs().max()) > 1e-4:
                for n in cams:
                    if torch.equal(imgs[n], prev_img[n]):
                        frozen += 1
            prev_img = {n: t.clone() for n, t in imgs.items()}
            prev_q = q_now
            for n in cams:
                buf.add(f"obs/images/{n}", imgs[n].cpu().numpy())
            buf.add("obs/state", torch.tensor(view.vla_vector("state")).numpy().astype("float32"))
            tcp_p, tcp_q = [], []
            for arm in c.robot.arms:
                p, q = view.tcp_pose_root(arm.name)
                tcp_p += p.tolist()
                tcp_q += q.tolist()
            buf.add("obs/tcp_pos", torch.tensor(tcp_p).numpy().astype("float32"))
            buf.add("obs/tcp_quat", torch.tensor(tcp_q).numpy().astype("float32"))
            op, oq = view.object_pose()
            buf.add("obs/object_pos", op.cpu().numpy().astype("float32"))
            obj_max_z = max(obj_max_z, float(op[2]))
            buf.add("obs/object_quat", oq.cpu().numpy().astype("float32"))
            # 行動
            action = cmd.raw if cmd.raw is not None else integ.action(cmd, view, max_step)
            env.step(action)
            render_fix(env, c.task)
            buf.add("teleop_actions", action[0].cpu().numpy().astype("float32"))
            buf.add("actions", torch.tensor(view.vla_vector("target")).numpy().astype("float32"))
            # 成功判定（置き場所に置かれ、グリッパが開いている状態が続いたら終了）
            open_now = all(cmd.gripper_open.get(g.name, True) for g in c.robot.grippers)
            held = held + 1 if view.task_success(open_now) else 0
            if held >= c.teleop.success_hold_steps:
                outcome = "success"
                break
            if cmd.done and device_kind in ("scripted", "mimic"):
                outcome = "failed"  # 動作を終えたのに成功していない（把持の失敗など）
                break
        frozen_total += frozen
        fp, _ = view.object_pose()
        tx, ty = c.task.target.pos
        ep = {
            "attempt": attempts,
            "object": view.object_cfg.key,
            "outcome": outcome,
            "steps": len(buf),
            "frozen_frames": frozen,
            # 失敗の切り分け用: 物体が持ち上がったか（lift）、どこに置かれたか
            "object_lift_m": round(obj_max_z - float(obj_p[2]), 4),
            "object_final": [round(float(x), 4) for x in fp],
            "object_to_target_m": round(((float(fp[0]) - tx) ** 2 + (float(fp[1]) - ty) ** 2) ** 0.5, 4),
            "tcp_displacement_m": {a: [round(float(x), 4) for x in (view.tcp_pose_root(a)[0] - p0)] for a, p0 in tcp_start.items()},
        }
        if device_kind == "scripted":
            ep["scripted_phase"] = device.phase
            ep["scripted_arm"] = device.arm
        episodes.append(ep)
        print(f"[urdf2vla] attempt {attempts}: {ep}", flush=True)
        if outcome != "success" and not saved_failure_frames:
            _save_failure_frames(project, buf, cams)
            saved_failure_frames = True
        if frozen > FROZEN_TOLERANCE:
            result.fail(
                f"カメラ画像が更新されていない step が {frozen} 回ありました（腕は動いていた）。"
                "このまま学習すると画像を使えないモデルになります。overrides.task.render_fix: true を試してください。"
            )
            break
        jump = max((_first_frame_jump(buf.data.get(f"obs/images/{n}", [])) for n in cams), default=0.0)
        stale_first = max(stale_first, jump)
        if outcome == "success":
            write_episode(
                h5, start_idx + saved, buf, initial,
                {"success": True, "device": device_kind, "seed": int(args.seed), "attempt": attempts,
                 "object": view.object_cfg.key, "language_instruction": view.instruction()},
            )  # fmt: skip
            if first_demo is None:
                first_demo = {
                    "actions": buf.data["actions"],
                    "states": buf.data["obs/state"],
                    "joint_pos0": initial["joint_pos"],
                    "object_pose0": initial["object_pose"],
                    "object_index": obj_index,
                    "object_place_pose": place_pose,
                }
            saved += 1
            h5.flush()
    data.attrs["total"] = len([k for k in data.keys() if k.startswith("demo_")])
    h5.close()

    checks = [{"name": "camera_frames_updated", "ok": frozen_total == 0, "detail": {"frozen_frames": frozen_total}}]
    # reset 直後の最初の画像が reset 前の場面のままだと、学習データの最初のフレームだけ別の場面になる
    checks.append({"name": "first_frame_fresh", "ok": stale_first < STALE_FIRST_FRAME_RATIO, "detail": {"max_jump_ratio": round(stale_first, 2)}})
    replay = None
    if first_demo is not None:
        replay = _replay_labels(env, view, c, first_demo)
        # 記録した行動ラベルだけで同じ腕の軌道が再現できるか（ラベルの正しさ）。物体の結果は物理のぶれで変わりうる
        # 接触後は物体との力のやり取りで軌道が変わりうるので、把持前の区間で判定する
        checks.append({"name": "label_replay", "ok": replay["free_motion_max_error_rad"] < REPLAY_TOLERANCE, "detail": replay})
        if not replay["task_success"]:
            result.warnings.append(
                "記録した行動を再生すると、腕は同じ軌道をたどりましたがタスクは成功しませんでした。"
                "把持が不安定で、同じ動きでも物体の結果が変わる状態です（グリッパのモデルや把持の高さを見直す余地があります）。"
            )
    elapsed = time.time() - t_start
    report = {
        "dataset": project.rel(path),
        "device": device_kind,
        "saved": saved,
        "attempts": attempts,
        "success_rate": round(saved / max(attempts, 1), 3),
        "episodes": episodes,
        "checks": checks,
        "fps": c.vla.control_hz,
        "elapsed_s": round(elapsed, 1),
        "size_mb": round(path.stat().st_size / 1e6, 1),
    }
    (project.build / "collect_report.yaml").write_text(dump_yaml(report))
    result.outputs["dataset"] = project.rel(path)
    result.outputs["collect_report"] = project.rel(project.build / "collect_report.yaml")
    result.data = {k: report[k] for k in ("dataset", "saved", "attempts", "success_rate", "size_mb", "elapsed_s")}
    result.data["checks"] = [f"{x['name']}: {'OK' if x['ok'] else 'FAIL'}" for x in checks]
    for x in checks:
        print(f"[urdf2vla] check {x['name']}: {'OK' if x['ok'] else 'FAIL'} {x['detail']}", flush=True)
        if not x["ok"] and result.status != "error":
            result.fail(f"検査 {x['name']} に失敗しました: {x['detail']}")
    if saved == 0 and result.status != "error":
        result.fail(f"成功したデモがありません（{attempts} 回試行）。build/collect_report.yaml の episodes で失敗の様子を確認してください。")
    elif saved < num_demos and result.status != "error":
        result.warnings.append(f"成功デモは {saved}/{num_demos} 本でした（{attempts} 回試行）。")
    if result.status == "ok":
        result.next = ["convert_dataset"]
