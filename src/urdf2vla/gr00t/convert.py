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


LAYOUT_FILE = "urdf2vla.json"  # meta/ の下。{"layout": "native" | "droid", ...}（train_vla・evaluate_vla が読む）


def dataset_layout(ds: Path) -> dict:
    """変換済みデータセットの並び（meta/urdf2vla.json。なければ urdf2vla の並び = native）。"""
    p = ds / "meta" / LAYOUT_FILE
    return json.loads(p.read_text()) if p.exists() else {"layout": "native"}


def _output_dir(project: Project, args, result: SkillResult) -> Path | None:
    suffix = "_droid_gr00t" if getattr(args, "layout", "native") == "droid" else "_gr00t"
    out = project.root / "datasets" / f"{args.dataset}{suffix}"
    if out.exists():
        if not args.force:
            result.fail(f"{project.rel(out)} は既にあります。作り直すなら --force を付けてください。")
            return None
        shutil.rmtree(out)
    return out


def _open_source(project: Project, src: Path, h5, result: SkillResult):
    from urdf2vla.schema.vla import VlaInterface

    data = h5["data"]
    # 旧名（easyVLA）で記録したデータは easyvla_format を持つ
    fmt = int(data.attrs.get("urdf2vla_format", data.attrs.get("easyvla_format", 1)))
    if fmt < 2:
        result.fail(
            f"{project.rel(src)} は Isaac Lab 2.x 版の urdf2vla で記録したデータです（urdf2vla_format {fmt}、四元数の順序が違う）。"
            "Isaac Lab 3.0 版の collect_dataset で記録し直してください。"
        )
        return None
    return VlaInterface.model_validate(json.loads(data.attrs["vla_interface"]))


def _write_meta(out: Path, robot_name: str, fps: float, episodes: list, tasks: dict, features: dict, n_videos: int, modality: dict, layout: dict) -> None:
    n, total = len(episodes), sum(e["length"] for e in episodes)
    info = {
        "codebase_version": "v2.1",
        "robot_type": robot_name,
        "total_episodes": n,
        "total_frames": total,
        "total_tasks": len(tasks),
        "total_videos": n * n_videos,
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
    (meta / "tasks.jsonl").write_text("".join(json.dumps({"task_index": i, "task": t}) + "\n" for t, i in tasks.items()))
    (meta / "modality.json").write_text(json.dumps(modality, indent=2))
    (meta / LAYOUT_FILE).write_text(json.dumps(layout, indent=2))


def _frame_table(state, action, fps: float, task_index: int, ep: int, total: int, task_col: str):
    import numpy as np
    import pandas as pd

    T = len(action)
    reward = np.zeros(T, dtype=np.float32)
    reward[-1] = 1.0
    return pd.DataFrame(
        {
            "observation.state": list(state),
            "action": list(action),
            "timestamp": (np.arange(T) / fps).astype(np.float32),
            task_col: np.full(T, task_index, dtype=np.int64),
            "task_index": np.full(T, task_index, dtype=np.int64),
            "episode_index": np.full(T, ep, dtype=np.int64),
            "index": np.arange(total, total + T, dtype=np.int64),
            "frame_index": np.arange(T, dtype=np.int64),
            "next.reward": reward,
            "next.done": np.arange(T) == T - 1,
        }
    )


def _scalar_features(features: dict, state_names: list[str], action_names: list[str], task_col: str) -> None:
    features["observation.state"] = {"dtype": "float32", "shape": [len(state_names)], "names": state_names}
    features["action"] = {"dtype": "float32", "shape": [len(action_names)], "names": action_names}
    for col, dtype in (("timestamp", "float32"), ("next.reward", "float32")):
        features[col] = {"dtype": dtype, "shape": [1]}
    for col in (task_col, "task_index", "episode_index", "index", "frame_index"):
        features[col] = {"dtype": "int64", "shape": [1]}
    features["next.done"] = {"dtype": "bool", "shape": [1]}


def run(project: Project, args, result: SkillResult) -> None:
    if getattr(args, "layout", "native") == "droid":
        return run_droid(project, args, result)
    import h5py
    import numpy as np

    from urdf2vla.gr00t.modality import modality_config_py, modality_json

    src = project.root / "datasets" / f"{args.dataset}.hdf5"
    if not src.exists():
        result.fail(f"{project.rel(src)} がありません。collect_dataset --dataset {args.dataset} を先に実行してください。")
        return
    out = _output_dir(project, args, result)
    if out is None:
        return

    with h5py.File(src, "r") as h5:
        data = h5["data"]
        vla = _open_source(project, src, h5, result)
        if vla is None:
            return
        fps = float(data.attrs["fps"])
        instruction = str(data.attrs.get("language_instruction", vla.language_instruction))
        robot_name = str(data.attrs.get("robot_name", project.name))
        demos = [n for n in _demo_order(data.keys()) if bool(data[n].attrs.get("success", True))]
        if not demos:
            result.fail("変換できる成功デモがありません。")
            return
        episodes, total = [], 0
        features: dict = {}
        tasks: dict[str, int] = {}  # 指示文 → task_index（エピソードごとに物体が違えば指示文も違う）
        for ep, name in enumerate(demos):
            g = data[name]
            ep_instruction = str(g.attrs.get("language_instruction", instruction))
            task_index = tasks.setdefault(ep_instruction, len(tasks))
            state = g["obs/state"][:].astype(np.float32)
            action = g["actions"][:].astype(np.float32)
            T = len(action)
            if state.shape != (T, vla.state.dimension) or action.shape != (T, vla.action.dimension):
                result.fail(f"{name}: state {state.shape} / action {action.shape} が vla_interface と合いません")
                return
            chunk = ep // CHUNK
            df = _frame_table(state, action, fps, task_index, ep, total, "annotation.human.task_description")
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
            episodes.append({"episode_index": ep, "tasks": [ep_instruction], "length": int(T)})
            total += T
            print(f"[urdf2vla] converted {name} -> episode {ep} ({T} steps)", flush=True)

    _scalar_features(features, vla.state.names, vla.action.names, "annotation.human.task_description")
    n = len(episodes)
    arms = sum(1 for g in vla.action.groups if g.kind == "joint_position")
    _write_meta(out, robot_name, fps, episodes, tasks, features, len(vla.images), modality_json(vla), {"layout": "native", "arms": arms})
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


def run_droid(project: Project, args, result: SkillResult) -> None:
    """--layout droid: GR00T の DROID の embodiment（OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT）の並びに変換する（単腕のみ）。
    外部カメラが複数（DROID の配置の left / right）なら、各デモを外部カメラごとに 1 エピソードずつ書く。"""
    import h5py
    import numpy as np
    import yaml

    from urdf2vla.gr00t import droid

    src = project.root / "datasets" / f"{args.dataset}.hdf5"
    if not src.exists():
        result.fail(f"{project.rel(src)} がありません。collect_dataset --dataset {args.dataset} を先に実行してください。")
        return
    cams_yaml = project.root / "generated" / "cameras.yaml"
    cameras = (yaml.safe_load(cams_yaml.read_text()) or {}).get("cameras", []) if cams_yaml.exists() else []
    exterior_arg = [e.strip() for e in (getattr(args, "exterior", None) or "").split(",") if e.strip()] or None
    out = _output_dir(project, args, result)
    if out is None:
        return

    with h5py.File(src, "r") as h5:
        data = h5["data"]
        vla = _open_source(project, src, h5, result)
        if vla is None:
            return
        try:
            n_arm = droid.single_arm_joints(vla)
            wrist, exteriors = droid.pick_cameras(cameras, [i.camera for i in vla.images], exterior_arg)
        except ValueError as e:
            result.fail(str(e))
            return
        fps = float(data.attrs["fps"])
        instruction = str(data.attrs.get("language_instruction", vla.language_instruction))
        robot_name = str(data.attrs.get("robot_name", project.name))
        demos = [n for n in _demo_order(data.keys()) if bool(data[n].attrs.get("success", True))]
        if not demos:
            result.fail("変換できる成功デモがありません。")
            return
        episodes, total, ep = [], 0, 0
        features: dict = {}
        tasks: dict[str, int] = {}
        sources = []  # エピソード → (デモ, 外部カメラ)
        for name in demos:
            g = data[name]
            if "obs/tcp_pos" not in g or "obs/tcp_quat" not in g:
                result.fail(f"{name}: obs/tcp_pos・obs/tcp_quat がありません（DROID の eef_9d に要る）。collect_dataset で記録し直してください。")
                return
            ep_instruction = str(g.attrs.get("language_instruction", instruction))
            task_index = tasks.setdefault(ep_instruction, len(tasks))
            state, action = g["obs/state"][:], g["actions"][:]
            T = len(action)
            s, a = droid.episode_vectors(g["obs/tcp_pos"][:, :3], g["obs/tcp_quat"][:, :4], state, action, n_arm)
            wrist_frames = np.stack([droid.resize_with_pad(f) for f in g[f"obs/images/{wrist}"][:]])
            for ext in exteriors:
                chunk = ep // CHUNK
                df = _frame_table(s, a, fps, task_index, ep, total, "annotation.language.language_instruction")
                pq = out / f"data/chunk-{chunk:03d}/episode_{ep:06d}.parquet"
                pq.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(pq)
                ext_frames = np.stack([droid.resize_with_pad(f) for f in g[f"obs/images/{ext}"][:]])
                for key, frames in ((droid.EXTERIOR_KEY, ext_frames), (droid.WRIST_KEY, wrist_frames)):
                    vid = out / f"videos/chunk-{chunk:03d}/observation.images.{key}/episode_{ep:06d}.mp4"
                    features[f"observation.images.{key}"] = _encode_video(frames, vid, fps)
                episodes.append({"episode_index": ep, "tasks": [ep_instruction], "length": int(T)})
                sources.append((name, ext))
                print(f"[urdf2vla] converted {name} (exterior {ext}) -> episode {ep} ({T} steps)", flush=True)
                total += T
                ep += 1

    names = [f"eef_9d_{i}" for i in range(9)] + ["gripper_position"] + [f"joint_position_{i}" for i in range(droid.DROID_JOINTS)]
    _scalar_features(features, names, names, "annotation.language.language_instruction")
    layout = {"layout": "droid", "embodiment_tag": droid.DROID_TAG, "arm_joints": n_arm, "wrist_camera": wrist,
              "exterior_cameras": exteriors, "video_delta_indices": [0]}  # fmt: skip
    _write_meta(out, robot_name, fps, episodes, tasks, features, 2, droid.modality_json(), layout)
    cfg_path = out / "urdf2vla_droid_modality.py"
    cfg_path.write_text(droid.modality_config_py())

    # 統計（正規化用）。相対行動（eef_9d・joint_position）の統計は登録済みの DROID の設定で作る
    sys.path.insert(0, str(out))
    __import__(cfg_path.stem)
    from gr00t.data.embodiment_tags import EmbodimentTag
    from gr00t.data.stats import generate_rel_stats, generate_stats

    generate_stats(out)
    generate_rel_stats(out, EmbodimentTag.OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT)

    checks = _validate_droid(out, src, sources[0], wrist)
    n = len(episodes)
    report = {"dataset": project.rel(out), "layout": layout, "episodes": n, "demos": len(demos), "frames": total, "fps": fps,
              "modality_config": project.rel(cfg_path), "checks": checks}  # fmt: skip
    (project.build / "convert_droid_report.yaml").write_text(dump_yaml(report))
    result.outputs["gr00t_dataset"] = project.rel(out)
    result.outputs["modality_config"] = project.rel(cfg_path)
    result.data = {"layout": "droid", "episodes": n, "demos": len(demos), "exterior_cameras": exteriors, "frames": total, "fps": fps,
                   "checks": [f"{c['name']}: {'OK' if c['ok'] else 'FAIL'}" for c in checks]}  # fmt: skip
    for c in checks:
        print(f"[urdf2vla] check {c['name']}: {'OK' if c['ok'] else 'FAIL'} {c['detail']}", flush=True)
        if not c["ok"]:
            result.fail(f"検査 {c['name']} に失敗しました: {c['detail']}")
    if result.status == "ok":
        result.next = ["train_vla", "evaluate_vla"]


def _validate_droid(out: Path, src: Path, first: tuple[str, str], wrist: str) -> list[dict]:
    import h5py
    import numpy as np

    from gr00t.configs.data.embodiment_configs import MODALITY_CONFIGS
    from gr00t.data.dataset.lerobot_episode_loader import LeRobotEpisodeLoader

    from urdf2vla.gr00t import droid

    checks = []
    loader = LeRobotEpisodeLoader(dataset_path=out, modality_configs=MODALITY_CONFIGS[droid.DROID_TAG_VALUE])
    ep = loader[0]
    cols = list(ep.columns)
    want = [f"{m}.{k}" for m in ("state", "action") for k, _ in droid.STATE_KEYS]
    lang_col = "language.annotation." + droid.LANGUAGE_KEY  # ローダの列名は "language." + modality config のキー
    want += [f"video.{droid.EXTERIOR_KEY}", f"video.{droid.WRIST_KEY}", lang_col]
    missing = [w for w in want if w not in cols]
    checks.append({"name": "gr00t_loader", "ok": not missing, "detail": {"episodes": len(loader), "missing_columns": missing, "columns": cols if missing else None}})
    if missing:
        return checks
    lang = str(ep[lang_col].iloc[0])
    checks.append({"name": "language", "ok": bool(lang.strip()), "detail": lang})
    eef = np.asarray(ep["state.eef_9d"].iloc[0], dtype=np.float64)
    rows = eef[3:].reshape(2, 3)  # 回転行列の 2 行: 単位長さで直交
    ortho = float(np.abs(rows @ rows.T - np.eye(2)).max())
    checks.append({"name": "eef_9d_rotation", "ok": ortho < 1e-3, "detail": {"max_orthonormal_error": round(ortho, 6)}})
    demo, ext = first
    with h5py.File(src, "r") as h5:
        for key, cam in ((droid.EXTERIOR_KEY, ext), (droid.WRIST_KEY, wrist)):
            ref = droid.resize_with_pad(h5[f"data/{demo}/obs/images/{cam}"][0]).astype(np.float32)
            frame = np.asarray(ep[f"video.{key}"].iloc[0]).astype(np.float32)
            err = float(np.abs(frame - ref).mean()) if frame.shape == ref.shape else float("inf")
            checks.append({"name": f"video_matches:{key}", "ok": err < 6.0, "detail": {"camera": cam, "mean_abs_error": round(err, 2), "shape": list(frame.shape)}})
    return checks
