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
    obs = {"video": {}, "state": {}, "language": {"annotation.human.task_description": [[vla.language_instruction]]}}
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
                out.append({"joint_pos": data[n]["initial_state/joint_pos"][:], "object_pose": data[n]["initial_state/object_pose"][:]})
    return out


def run(project: Project, args, result: SkillResult, app=None) -> None:
    from urdf2vla.gr00t.client import Gr00tClient
    from urdf2vla.sim.common import load_configs, make_env
    from urdf2vla.sim.lab import render_fix, write_joint_state, write_root_pose
    from urdf2vla.sim.state import SimView

    c = load_configs(project)
    vla = c.vla
    env, env_id = make_env(c, "policy")
    view = SimView(env, c.robot, c.task, vla)
    client = Gr00tClient(port=int(args.port))
    if not client.wait_ready(300):
        result.fail(f"GR00T サーバ（127.0.0.1:{args.port}）に接続できません。")
        return
    replay = bool(args.replay)
    inits = _initial_states(project, args.dataset) if replay else []
    lengths = []
    if replay:
        meta = project.root / "datasets" / f"{args.dataset}_gr00t" / "meta" / "episodes.jsonl"
        lengths = [json.loads(line)["length"] for line in meta.read_text().splitlines()]
    n_eps = min(int(args.episodes), len(inits)) if replay else int(args.episodes)
    exec_h = int(args.execution_horizon)
    max_steps = int(c.task.episode_length_s * c.task.control_hz)
    hold = 10
    torch.manual_seed(int(args.seed))
    print(f"[urdf2vla] evaluating on {env_id}: {n_eps} episodes, mode={'replay' if replay else 'model'}", flush=True)
    cams = [i.camera for i in vla.images]
    video_dir = project.build / "eval_videos"
    video_prefix = f"replay_{args.dataset}" if replay else "model"

    episodes = []
    frozen_total = 0
    project.snapshots.mkdir(parents=True, exist_ok=True)
    for ep in range(n_eps):
        env.reset()
        if replay:
            q0 = torch.tensor(inits[ep]["joint_pos"], device=env.device).unsqueeze(0)
            write_joint_state(view.robot, q0)
            pose = torch.tensor(inits[ep]["object_pose"], device=env.device).unsqueeze(0)
            pose[:, :3] += view.origin
            write_root_pose(view.object, pose)
            env.scene.write_data_to_sim()
            env.sim.step(render=True)
            env.scene.update(env.physics_dt)
            client.reset({"episode_index": ep, "step_index": 0})
        else:
            client.reset()
        render_fix(env, c.task)
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
            if not queue:
                action = client.get_action(build_observation(view, vla))
                queue = chunk_to_env_actions(action, vla, exec_h)
                calls += 1
                if calls == 1 and not replay:
                    _check_absolute(queue[0], view, vla)
            a = queue.pop(0)
            gripper_open = all(x > 0 for x in a[len(a) - len(c.robot.grippers) :])
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
            "outcome": outcome,
            "steps": step,
            "policy_calls": calls,
            "object_to_target_m": round(((float(pos[0]) - tx) ** 2 + (float(pos[1]) - ty) ** 2) ** 0.5, 4),
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
