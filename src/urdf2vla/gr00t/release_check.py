"""学習した checkpoint の簡単な検査（シミュレーションなし、1〜2 分）: グリッパを開く場面で、モデルが開くか。

Isaac Sim での評価（evaluate_vla）は 20 エピソードで 30 分ほどかかる。その前に、学習データのエピソードで
デモがグリッパを開く step の少し前（開くのが、評価で実行する先頭 execution_horizon step に入る位置）からモデルに
問い合わせ、予測した行動のその範囲でグリッパが開くかを数える。開かないなら、評価しても「掴んで運ぶが離さない」になる。
（FANUC の DROID の並びの最初のモデルで実際に起きた。学習の損失は十分に下がっていた。）
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

OFFSETS = (4, 10)  # 開く step の何 step 前から問い合わせるか（どちらも実行される範囲 16 step の内側）
PASS_RATE = 0.5


def _is_open(v: float, open_is_high: bool, threshold: float = 0.5) -> bool:
    return v >= threshold if open_is_high else v < threshold


def release_step(labels: np.ndarray, open_is_high: bool) -> int | None:
    """グリッパの行動のラベルで、最後に「閉 → 開」になった step。"""
    o = np.array([_is_open(float(v), open_is_high) for v in np.asarray(labels).reshape(len(labels), -1)[:, 0]])
    idx = np.nonzero(o[1:] & ~o[:-1])[0]
    return int(idx[-1] + 1) if len(idx) else None


def run(checkpoint: Path, dataset: Path, tag: str, gripper_key: str, open_is_high: bool,
        episodes: int = 5, execution_horizon: int = 16) -> dict:  # fmt: skip
    import torch

    from gr00t.data.dataset.lerobot_episode_loader import LeRobotEpisodeLoader
    from gr00t.data.dataset.sharded_single_step_dataset import extract_step_data
    from gr00t.data.embodiment_tags import EmbodimentTag
    from gr00t.policy.gr00t_policy import Gr00tPolicy

    policy = Gr00tPolicy(embodiment_tag=tag, model_path=str(checkpoint), device="cuda" if torch.cuda.is_available() else "cpu")
    mc = policy.get_modality_config()
    loader = LeRobotEpisodeLoader(dataset_path=dataset, modality_configs=mc)
    etag = EmbodimentTag.resolve(tag)
    lang_key = mc["language"].modality_keys[0]
    picks = sorted({int(i) for i in np.linspace(0, len(loader) - 1, min(episodes, len(loader)))})
    queries, hits, per_episode = 0, 0, []
    for ep in picks:
        traj = loader[ep]
        labels = np.stack([np.asarray(v, dtype=np.float32).reshape(-1) for v in traj[f"action.{gripper_key}"]])
        r = release_step(labels, open_is_high)
        if r is None:
            per_episode.append({"episode": ep, "release_step": None})
            continue
        res = []
        for k in OFFSETS:
            t = r - k
            if t < 0:
                continue
            d = extract_step_data(traj, t, mc, etag, allow_padding=True)
            obs = {
                "video": {key: np.asarray(v)[None] for key, v in d.images.items()},
                "state": {key: np.asarray(v, dtype=np.float32)[None] for key, v in d.states.items()},
                "language": {lang_key: [[d.text or ""]]},
            }
            action, _ = policy.get_action(obs)
            pred = np.asarray(action[gripper_key])[0, :execution_horizon].reshape(-1)
            opened = next((j for j, v in enumerate(pred) if _is_open(float(v), open_is_high)), None)
            queries += 1
            hits += opened is not None
            res.append({"steps_before_release": k, "opened_at": opened})
        per_episode.append({"episode": ep, "release_step": r, "queries": res})
    rate = hits / queries if queries else 0.0
    return {
        "ok": queries > 0 and rate >= PASS_RATE,
        "release_rate": round(rate, 3),
        "queries": queries,
        "execution_horizon": execution_horizon,
        "checkpoint": str(checkpoint),
        "episodes": per_episode,
    }
