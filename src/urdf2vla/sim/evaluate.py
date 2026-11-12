"""evaluate_vla（Isaac Sim 側）: GR00T 推論サーバに観測を送り、返ってきた行動を評価環境で実行して成功率を測る。

評価環境（policy）の行動 = [arm ごとの関節目標（絶対値）..., グリッパごとの ±1]。
GR00T の行動はグループ（vla_interface の action.groups）ごとの (1, horizon, dim) で返る。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import torch

from urdf2vla.project import Project, dump_yaml
from urdf2vla.result import SkillResult


def build_observation(view, vla) -> dict:
    imgs = view.images([i.camera for i in vla.images])
    state = view.vla_vector("state")
    # 指示文はエピソードで選んだ物体のもの（"put the apple in the tray" など）
    obs = {"video": {}, "state": {}, "language": {"annotation.human.task_description": [[view.instruction()]]}}
    for i in vla.images:
        obs["video"][i.key] = imgs[i.camera].cpu().numpy().astype(np.uint8)[None, None]
    k = 0
    for g in vla.state.groups:
        obs["state"][g.name] = np.asarray(state[k : k + g.dim], dtype=np.float32)[None, None]
        k += g.dim
    return obs


def chunk_to_env_actions(action: dict, vla, n_steps: int) -> list[list[float]]:
    """GR00T の行動（グループごと）→ 評価環境の行動ベクトルの列。"""
    out = []
    horizon = min(int(np.asarray(next(iter(action.values()))).shape[1]), n_steps)
    for t in range(horizon):
        arm, grip = [], []
        for g in vla.action.groups:
            v = np.asarray(action[g.name])[0, t]
            if g.kind == "joint_position":
                arm += [float(x) for x in v]
            else:
                grip.append(1.0 if float(v[0]) >= vla.action.gripper_threshold else -1.0)
        out.append(arm + grip)
    return out


# ---- GR00T の DROID の事前学習（embodiment OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT）で zero-shot に動かす
# Isaac-GR00T の examples/DROID/main_gr00t.py（実機の DROID の client）と同じ観測・行動にする
DROID_RESOLUTION = (180, 320)  # (高さ, 幅)。正方形の画像は左右を黒で埋める（tf.image.resize_with_pad と同じ）
DROID_EEF_ROTATION_CORRECT = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=np.float64)


def _resize_with_pad(img: np.ndarray, height: int, width: int) -> np.ndarray:
    from PIL import Image

    h, w = img.shape[:2]
    s = min(height / h, width / w)
    nh, nw = int(round(h * s)), int(round(w * s))
    out = np.zeros((height, width, 3), dtype=np.uint8)
    top, left = (height - nh) // 2, (width - nw) // 2
    out[top : top + nh, left : left + nw] = np.asarray(Image.fromarray(img).resize((nw, nh), Image.BILINEAR))
    return out


def droid_eef_9d(view, arm: str) -> np.ndarray:
    """DROID の eef_9d: ロボットの base 座標での TCP の位置 + 回転（補正の行列を掛けた回転行列の上 2 行）。
    回転は panda_hand と同じ向きの TCP を使う（DROID の統計の平均とこの向きが一致する）。"""
    from isaaclab.utils.math import matrix_from_quat

    p, q = view.tcp_pose_root(arm)
    r = matrix_from_quat(q.unsqueeze(0))[0].cpu().numpy().astype(np.float64) @ DROID_EEF_ROTATION_CORRECT
    return np.concatenate([p.cpu().numpy(), r[:2, :].reshape(6)]).astype(np.float32)


class DroidObservation:
    """DROID の観測（外部カメラ 1 台 + 手先カメラ、各 2 フレーム）を毎 step 積む。"""

    def __init__(self, view, vla, cameras):
        arm = view.cfg.arms[0]
        if len(view.cfg.arms) != 1 or len(arm.joints) != 7 or not view.cfg.grippers:
            raise RuntimeError("GR00T の DROID は 7 関節の腕とグリッパのロボット（Franka）向けです。")
        wrist = next((c.name for c in cameras.cameras if c.mount == "link"), None)
        ext = next((c.name for c in cameras.cameras if c.mount == "world"), None)
        if wrist is None or ext is None:
            raise RuntimeError("GR00T の DROID には手先カメラと外部カメラが 1 台ずつ必要です（DROID の配置: wrist + left/right）。")
        self.view, self.vla, self.arm, self.cams = view, vla, arm.name, (ext, wrist)
        self.buf: list[dict] = []
        self.video_delta = [0]

    def configure(self, client) -> None:
        """サーバのモデルの video の delta_indices（例: 事前学習 [-15, 0] = 今と 15 step 前、DROID の fine-tune [0]）を使う。"""
        mc = client.call("get_modality_config")
        video = mc["video"]
        video = video.get("as_json", video) if isinstance(video, dict) else video
        self.video_delta = list(video["delta_indices"]) if isinstance(video, dict) else list(video.delta_indices)
        print(f"[urdf2vla] DROID video delta_indices: {self.video_delta}", flush=True)

    @property
    def history(self) -> int:
        return max(-min(self.video_delta), 0) + 1

    def reset(self) -> None:
        self.buf = []

    def push(self) -> None:
        imgs = self.view.images(list(self.cams))
        self.buf.append({k: _resize_with_pad(imgs[k].cpu().numpy().astype(np.uint8), *DROID_RESOLUTION) for k in self.cams})
        self.buf = self.buf[-self.history :]

    def observation(self) -> dict:
        ext, wrist = self.cams
        # delta_indices の各フレーム（足りない間は最も古いフレーム）
        frames = [self.buf[max(len(self.buf) - 1 + d, 0)] for d in self.video_delta]
        g = self.view.cfg.grippers[0].name
        return {
            "video": {
                "exterior_image_1_left": np.stack([f[ext] for f in frames])[None],
                "wrist_image_left": np.stack([f[wrist] for f in frames])[None],
            },
            "state": {
                "eef_9d": droid_eef_9d(self.view, self.arm)[None, None],
                "gripper_position": np.array([[[1.0 - self.view.gripper_scalar(g, "state")]]], dtype=np.float32),  # DROID は 1 = 閉
                "joint_position": np.asarray(self.view.vla_vector("state")[:7], dtype=np.float32)[None, None],
            },
            "language": {"annotation.language.language_instruction": [[self.view.instruction()]]},
        }


def droid_chunk_to_env_actions(action: dict, n_steps: int) -> list[list[float]]:
    """DROID の行動（関節位置の絶対値 7 + グリッパの位置 1。0.5 より大きければ閉）→ 評価環境の行動ベクトルの列。"""
    joints = np.asarray(action["joint_position"])[0]
    grip = np.asarray(action["gripper_position"])[0]
    return [[float(x) for x in joints[t]] + [-1.0 if float(grip[t][0]) > 0.5 else 1.0] for t in range(min(len(joints), n_steps))]


def _check_absolute(first: list[float], view, vla) -> None:
    """GR00T が関節目標の絶対値ではなく相対値（state との差）を返していないか。
    相対値をそのまま関節目標にすると、腕が 0 rad 付近へ向かって暴れる（気づきにくい失敗）。"""
    n_arm = sum(g.dim for g in vla.action.groups if g.kind == "joint_position")
    state = view.vla_vector("state")[:n_arm]
    if max(abs(x) for x in first[:n_arm]) < 0.05 and max(abs(x) for x in state) > 0.2:
        raise RuntimeError(
            "GR00T が関節目標の絶対値ではなく相対値を返しているようです（最初の行動がほぼ 0、現在の関節角は 0 から離れている）。"
            "checkpoint の学習設定（use_relative_action）と modality config を確認してください。"
        )


class _VideoWriter:
    """1 エピソードの動画。sim コンテナには PyAV がないので、生の RGB を ffmpeg の標準入力に流す。
    entry は最後に os._exit で抜けるので、close()（ffmpeg の終了待ち）をしないと mp4 が壊れる。"""

    def __init__(self, path: Path, fps: float):
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("--video には ffmpeg が必要です（imageio_ffmpeg も ffmpeg コマンドも見つかりません）。")
        self.exe, self.path, self.fps = exe, path, fps
        self.proc: subprocess.Popen | None = None

    def write(self, frame: np.ndarray) -> None:
        if self.proc is None:
            h, w = frame.shape[:2]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            cmd = [self.exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                   "-r", f"{self.fps:g}", "-i", "-", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(self.path)]  # fmt: skip
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self) -> None:
        if self.proc is None:
            return
        self.proc.stdin.close()
        if self.proc.wait(timeout=120) != 0:
            raise RuntimeError(f"ffmpeg が失敗しました（{self.path}）")
        self.proc = None


def _video_frame(imgs: dict, vla) -> np.ndarray:
    """VLA に渡すカメラ画像を左から vla.images の順に並べた 1 枚（高さが違えば下を黒で埋める）。"""
    tiles = [imgs[i.camera].cpu().numpy().astype(np.uint8) for i in vla.images]
    h = max(t.shape[0] for t in tiles)
    tiles = [np.pad(t, ((0, h - t.shape[0]), (0, 0), (0, 0))) for t in tiles]
    return np.concatenate(tiles, axis=1)


def _initial_states(project: Project, dataset: str) -> list[dict]:
    """replay 用: 記録したデモの初期状態（GR00T データセットのエピソード順 = demo 番号順）。"""
    import h5py

    from urdf2vla.gr00t.convert import _demo_order

    out = []
    with h5py.File(project.root / "datasets" / f"{dataset}.hdf5", "r") as h5:
        data = h5["data"]
        for n in _demo_order(data.keys()):
            if bool(data[n].attrs.get("success", True)):
                ini = data[n]["initial_state"]
                out.append(
                    {
                        "joint_pos": ini["joint_pos"][:],
                        "object_pose": ini["object_pose"][:],
                        "object_index": int(ini["object_index"][0]) if "object_index" in ini else 0,  # 物体が 1 つだった頃の記録は 0
                        "object_place_pose": ini["object_place_pose"][:] if "object_place_pose" in ini else None,
                    }
                )
    return out


def run(project: Project, args, result: SkillResult, app=None) -> None:
    from urdf2vla.gr00t.client import Gr00tClient
    from urdf2vla.sim.common import load_configs, make_env
    from urdf2vla.sim.lab import render_fix, write_joint_state
    from urdf2vla.sim.state import SimView

    c = load_configs(project)
    vla = c.vla
    env, env_id = make_env(c, "policy")
    view = SimView(env, c.robot, c.task, vla, project_root=project.root)
    client = Gr00tClient(port=int(args.port))
    if not client.wait_ready(300):
        result.fail(f"GR00T サーバ（127.0.0.1:{args.port}）に接続できません。")
        return
    if getattr(args, "droid", False):
        droid_obs = DroidObservation(view, vla, c.cameras)
        droid_obs.configure(client)
    replay = bool(args.replay)
    droid = droid_obs if getattr(args, "droid", False) else None
    inits = _initial_states(project, args.dataset) if replay else []
    lengths = []
    if replay:
        meta = project.root / "datasets" / f"{args.dataset}_gr00t" / "meta" / "episodes.jsonl"
        lengths = [json.loads(line)["length"] for line in meta.read_text().splitlines()]
    n_eps = min(int(args.episodes), len(inits)) if replay else int(args.episodes)
    exec_h = int(args.execution_horizon)
    max_steps = int(c.task.episode_length_s * c.task.control_hz)
    hold = 10
    torch.manual_seed(int(args.seed))  # 環境の作成の後（Isaac Lab が乱数の種を設定し直すため）
    print(f"[urdf2vla] evaluating on {env_id}: {n_eps} episodes, mode={'replay' if replay else 'model'}", flush=True)
    cams = [i.camera for i in vla.images]
    video_dir = project.build / "eval_videos"
    video_prefix = f"replay_{args.dataset}" if replay else "model"

    episodes = []
    frozen_total = 0
    project.snapshots.mkdir(parents=True, exist_ok=True)
    for ep in range(n_eps):
        env.reset()
        if droid is not None:
            droid.reset()
        if replay:
            q0 = torch.tensor(inits[ep]["joint_pos"], device=env.device).unsqueeze(0)
            write_joint_state(view.robot, q0)
            ini = inits[ep]
            if ini["object_place_pose"] is not None:  # 記録と同じ姿勢から同じ手順で置く（落ち着いた後まで記録と同じになる）
                view.place_object(ini["object_index"], [float(v) for v in ini["object_place_pose"]])
            else:  # 古い記録: 落ち着いた後の姿勢から置き直す
                view.replay_object(ini["object_index"], [float(v) for v in ini["object_pose"]])
            client.reset({"episode_index": ep, "step_index": 0})
        else:
            view.start_episode()
            client.reset()
        render_fix(env, c.task)
        start_p = view.object_center().clone()
        max_lift = 0.0
        moved_before_grasp = None
        writer = _VideoWriter(video_dir / f"{video_prefix}_ep{ep:03d}.mp4", c.task.control_hz) if args.video else None
        if writer:
            writer.write(_video_frame(view.images(cams), vla))
        steps_limit = min(max_steps, lengths[ep]) if replay else max_steps
        queue: list[list[float]] = []
        held, step, frozen, calls = 0, 0, 0, 0
        prev = None
        outcome = "timeout"
        gripper_open = True
        while step < steps_limit:
            if droid is not None:
                droid.push()  # 15 step 前の画像を渡すので、問い合わせない step も積む
            if not queue:
                if droid is not None:
                    action = client.get_action(droid.observation())
                    queue = droid_chunk_to_env_actions(action, exec_h)
                else:
                    action = client.get_action(build_observation(view, vla))
                    queue = chunk_to_env_actions(action, vla, exec_h)
                calls += 1
                if calls == 1 and not replay and droid is None:
                    _check_absolute(queue[0], view, vla)
            a = queue.pop(0)
            gripper_open = all(x > 0 for x in a[len(a) - len(c.robot.grippers) :])
            if not gripper_open and moved_before_grasp is None:  # 最初に閉じた時点で物体が初めの位置からどれだけ動いたか
                moved_before_grasp = float(torch.linalg.norm(view.object_center()[:2] - start_p[:2]))
            q_before = view.joint_pos().clone()
            env.step(torch.tensor([a], device=env.device))
            render_fix(env, c.task)
            step += 1
            imgs = view.images(cams)
            if writer:
                writer.write(_video_frame(imgs, vla))
            img = imgs[cams[0]]
            if prev is not None and torch.equal(img, prev) and float((view.joint_pos() - q_before).abs().max()) > 1e-4:
                frozen += 1
            prev = img.clone()
            max_lift = max(max_lift, float(view.object_center()[2] - start_p[2]))
            held = held + 1 if view.task_success(gripper_open) else 0
            if held >= hold:
                outcome = "success"
                break
        if replay and outcome != "success" and view.task_success(gripper_open):
            outcome = "success"  # 再生データの最後で成功状態にある
        if writer:
            writer.close()
        frozen_total += frozen
        pos, _ = view.object_pose()
        tx, ty = c.task.target.pos
        ep_info = {
            "episode": ep,
            "object": view.object_cfg.key,
            "instruction": view.instruction(),
            "outcome": outcome,
            "steps": step,
            "policy_calls": calls,
            "object_to_target_m": round(((float(pos[0]) - tx) ** 2 + (float(pos[1]) - ty) ** 2) ** 0.5, 4),
            "object_start": [round(float(v), 4) for v in start_p],
            "object_lift_m": round(max_lift, 4),
            "object_moved_before_grasp_m": None if moved_before_grasp is None else round(moved_before_grasp, 4),
            "frozen_frames": frozen,
        }
        if writer:
            ep_info["video"] = project.rel(writer.path)
        episodes.append(ep_info)
        print(f"[urdf2vla] episode {ep}: {ep_info}", flush=True)
        if ep == 0:
            from PIL import Image

            for i in vla.images:
                Image.fromarray(view.images([i.camera])[i.camera].cpu().numpy()).save(project.snapshots / f"eval_{i.key}_end.png")

    n_success = sum(e["outcome"] == "success" for e in episodes)
    checks = [{"name": "camera_frames_updated", "ok": frozen_total == 0, "detail": {"frozen_frames": frozen_total}}]
    report = {
        "env_id": env_id,
        "mode": "replay" if replay else "model",
        "model": getattr(args, "model", None),
        "episodes": episodes,
        "success_rate": round(n_success / max(len(episodes), 1), 3),
        "execution_horizon": exec_h,
        "checks": checks,
    }
    (project.build / "eval_report.yaml").write_text(dump_yaml(report))
    result.outputs["eval_report"] = project.rel(project.build / "eval_report.yaml")
    if args.video:
        result.outputs["eval_videos"] = project.rel(video_dir)  # エピソードごとのファイル名は eval_report の video
    result.data = {"mode": report["mode"], "success_rate": report["success_rate"], "successes": n_success, "episodes": len(episodes)}
    result.data["checks"] = [f"{x['name']}: {'OK' if x['ok'] else 'FAIL'}" for x in checks]
    for x in checks:
        if not x["ok"]:
            result.fail(f"検査 {x['name']} に失敗しました: {x['detail']}")
