"""convert_dataset: urdf2vla の HDF5 → GR00T（LeRobot v2.1）データセット（urdf2vla-gr00t コンテナ内）。

HDF5 の属性（vla_interface・fps・言語指示）だけで変換できるので、ロボットごとの設定ファイルは要らない。
既存の変換スクリプトで問題になった点を避けている:
- demo_10 が demo_2 より先に来る文字列順 → 番号順に並べる
- 出力先があると対話で止まる → --force で作り直す、なければエラーで止める
- splits / total_chunks の固定値 → エピソード数から計算する
- 統計（stats.json / relative_stats.json）→ 変換時に作り、学習・推論サーバがすぐ読めるようにする
"""

from __future__ import annotations

import json
import math
import re
import shutil
import sys
from pathlib import Path

from urdf2vla.project import Project, dump_yaml
from urdf2vla.result import SkillResult

CHUNK = 1000


def _encode_video(frames, path: Path, fps: float) -> dict:
    import av

    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = frames.shape[1:3]
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("h264", rate=round(fps))
        stream.width, stream.height = w, h
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "18", "g": "8"}  # 高画質・短い GOP（学習時のランダムアクセスを速く）
        for f in frames:
            for packet in stream.encode(av.VideoFrame.from_ndarray(f, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return {
        "dtype": "video",
        "shape": [int(h), int(w), 3],
        "names": ["height", "width", "channel"],
        "video_info": {
            "video.width": int(w),
            "video.height": int(h),
            "video.fps": float(fps),
            "video.codec": "h264",
            "video.pix_fmt": "yuv420p",
            "video.channels": 3,
            "video.is_depth_map": False,
            "has_audio": False,
        },
    }


def _demo_order(names) -> list[str]:
    return sorted((n for n in names if re.fullmatch(r"demo_\d+", n)), key=lambda n: int(n.split("_")[1]))


def run(project: Project, args, result: SkillResult) -> None:
    import h5py
    import numpy as np
    import pandas as pd

    from urdf2vla.gr00t.modality import modality_config_py, modality_json
    from urdf2vla.schema.vla import VlaInterface

    src = project.root / "datasets" / f"{args.dataset}.hdf5"
    if not src.exists():
        result.fail(f"{project.rel(src)} がありません。collect_dataset --dataset {args.dataset} を先に実行してください。")
        return
    out = project.root / "datasets" / f"{args.dataset}_gr00t"
    if out.exists():
        if not args.force:
            result.fail(f"{project.rel(out)} は既にあります。作り直すなら --force を付けてください。")
            return
        shutil.rmtree(out)

    with h5py.File(src, "r") as h5:
        data = h5["data"]
        # 旧名（easyVLA）で記録したデータは easyvla_format を持つ
        fmt = int(data.attrs.get("urdf2vla_format", data.attrs.get("easyvla_format", 1)))
        if fmt < 2:
            result.fail(
                f"{project.rel(src)} は Isaac Lab 2.x 版の urdf2vla で記録したデータです（urdf2vla_format {fmt}、四元数の順序が違う）。"
                "Isaac Lab 3.0 版の collect_dataset で記録し直してください。"
            )
            return
        vla = VlaInterface.model_validate(json.loads(data.attrs["vla_interface"]))
        fps = float(data.attrs["fps"])
        instruction = str(data.attrs.get("language_instruction", vla.language_instruction))
        robot_name = str(data.attrs.get("robot_name", project.name))
        demos = [n for n in _demo_order(data.keys()) if bool(data[n].attrs.get("success", True))]
        if not demos:
            result.fail("変換できる成功デモがありません。")
            return
        episodes, total = [], 0
        features: dict = {}
        for ep, name in enumerate(demos):
            g = data[name]
            state = g["obs/state"][:].astype(np.float32)
            action = g["actions"][:].astype(np.float32)
            T = len(action)
            if state.shape != (T, vla.state.dimension) or action.shape != (T, vla.action.dimension):
                result.fail(f"{name}: state {state.shape} / action {action.shape} が vla_interface と合いません")
                return
            chunk = ep // CHUNK
            reward = np.zeros(T, dtype=np.float32)
            reward[-1] = 1.0
            df = pd.DataFrame(
                {
                    "observation.state": list(state),
                    "action": list(action),
                    "timestamp": (np.arange(T) / fps).astype(np.float32),
                    "annotation.human.task_description": np.zeros(T, dtype=np.int64),
                    "task_index": np.zeros(T, dtype=np.int64),
                    "episode_index": np.full(T, ep, dtype=np.int64),
                    "index": np.arange(total, total + T, dtype=np.int64),
                    "frame_index": np.arange(T, dtype=np.int64),
                    "next.reward": reward,
                    "next.done": np.arange(T) == T - 1,
                }
            )
            pq = out / f"data/chunk-{chunk:03d}/episode_{ep:06d}.parquet"
            pq.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(pq)
            for img in vla.images:
                frames = g[f"obs/images/{img.camera}"][:]
                if frames.shape[0] != T:
                    result.fail(f"{name}: 画像 {img.camera} のフレーム数 {frames.shape[0]} が step 数 {T} と合いません")
                    return
                vid = out / f"videos/chunk-{chunk:03d}/observation.images.{img.key}/episode_{ep:06d}.mp4"
                features[f"observation.images.{img.key}"] = _encode_video(frames, vid, fps)
            episodes.append({"episode_index": ep, "tasks": [instruction], "length": int(T)})
            total += T
            print(f"[urdf2vla] converted {name} -> episode {ep} ({T} steps)", flush=True)

    features["observation.state"] = {"dtype": "float32", "shape": [vla.state.dimension], "names": vla.state.names}
    features["action"] = {"dtype": "float32", "shape": [vla.action.dimension], "names": vla.action.names}
    for col, dtype in (("timestamp", "float32"), ("next.reward", "float32")):
        features[col] = {"dtype": dtype, "shape": [1]}
    for col in ("annotation.human.task_description", "task_index", "episode_index", "index", "frame_index"):
        features[col] = {"dtype": "int64", "shape": [1]}
    features["next.done"] = {"dtype": "bool", "shape": [1]}

    n = len(episodes)
    info = {
        "codebase_version": "v2.1",
        "robot_type": robot_name,
        "total_episodes": n,
        "total_frames": total,
        "total_tasks": 1,
        "total_videos": n * len(vla.images),
        "total_chunks": math.ceil(n / CHUNK),
        "chunks_size": CHUNK,
        "fps": fps,
        "splits": {"train": f"0:{n}"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": features,
    }
    meta = out / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "info.json").write_text(json.dumps(info, indent=2))
    (meta / "episodes.jsonl").write_text("".join(json.dumps(e) + "\n" for e in episodes))
    (meta / "tasks.jsonl").write_text(json.dumps({"task_index": 0, "task": instruction}) + "\n")
    (meta / "modality.json").write_text(json.dumps(modality_json(vla), indent=2))
    module = f"urdf2vla_modality_{re.sub(r'[^a-zA-Z0-9]', '_', project.name)}"
    cfg_path = out / f"{module}.py"
    cfg_path.write_text(modality_config_py(vla))

    # 統計（正規化用）。相対行動の統計は modality config の登録が必要
    sys.path.insert(0, str(out))
    __import__(module)
    from gr00t.data.embodiment_tags import EmbodimentTag
    from gr00t.data.stats import generate_rel_stats, generate_stats

    generate_stats(out)
    generate_rel_stats(out, EmbodimentTag.NEW_EMBODIMENT)

    # 検証: GR00T のローダで読めるか、動画の 1 フレーム目が元画像と一致するか
    checks = _validate(out, src, demos[0], vla)
    report = {
        "dataset": project.rel(out),
        "episodes": n,
        "frames": total,
        "fps": fps,
        "modality_config": project.rel(cfg_path),
        "checks": checks,
    }
    (project.build / "convert_report.yaml").write_text(dump_yaml(report))
    result.outputs["gr00t_dataset"] = project.rel(out)
    result.outputs["modality_config"] = project.rel(cfg_path)
    result.data = {"episodes": n, "frames": total, "fps": fps, "checks": [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'}" for c in checks]}
    for c in checks:
        print(f"[urdf2vla] check {c['name']}: {'OK' if c['ok'] else 'FAIL'} {c['detail']}", flush=True)
        if not c["ok"]:
            result.fail(f"検査 {c['name']} に失敗しました: {c['detail']}")
    if result.status == "ok":
        result.next = ["train_vla", "evaluate_vla"]


def _validate(out: Path, src: Path, first_demo: str, vla) -> list[dict]:
    import h5py
    import numpy as np

    from gr00t.configs.data.embodiment_configs import MODALITY_CONFIGS
    from gr00t.data.dataset.lerobot_episode_loader import LeRobotEpisodeLoader

    checks = []
    loader = LeRobotEpisodeLoader(dataset_path=out, modality_configs=MODALITY_CONFIGS["new_embodiment"])
    ep = loader[0]
    cols = list(ep.columns)
    want = [f"state.{g.name}" for g in vla.state.groups] + [f"action.{g.name}" for g in vla.action.groups]
    want += [f"video.{i.key}" for i in vla.images]
    missing = [w for w in want if w not in cols]
    checks.append({"name": "gr00t_loader", "ok": not missing, "detail": {"episodes": len(loader), "missing_columns": missing}})
    # 動画が正しく書けているか（色の並び・解像度）: 先頭フレームを元の画像と比べる
    with h5py.File(src, "r") as h5:
        for img in vla.images:
            ref = h5[f"data/{first_demo}/obs/images/{img.camera}"][0].astype(np.float32)
            frame = np.asarray(ep[f"video.{img.key}"].iloc[0]).astype(np.float32)
            err = float(np.abs(frame - ref).mean()) if frame.shape == ref.shape else float("inf")
            checks.append({"name": f"video_matches:{img.key}", "ok": err < 6.0, "detail": {"mean_abs_error": round(err, 2), "shape": list(frame.shape)}})
    return checks
