"""GR00T N1.7 の DROID の embodiment（OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT）に合わせる変換。純 Python（numpy）。

単腕のロボット（Franka・FANUC など）は、DROID で学習したモデル（nvidia/GR00T-N1.7-DROID）から、DROID の embodiment の
まま fine-tune できる。DROID の state / action の層をそのまま使えるので、新しい embodiment で層を一から学習するより移りやすい。

DROID の embodiment（Isaac-GR00T の gr00t/configs/data/embodiment_configs.py）:
- video: exterior_image_1_left（外部カメラ 1 台）+ wrist_image_left（手先）。DROID の記録は外部カメラ 2 台だが、モデルは 1 台だけ見る
- state / action: eef_9d（base 座標の TCP の位置 3 + 回転行列の上 2 行 6）、gripper_position（1 = 閉）、joint_position（7 関節）
- action は eef と関節が state からの相対、グリッパは絶対値

urdf2vla の対応:
- 外部カメラが 2 台（DROID の配置の left / right）なら、各デモを両方のカメラで 1 エピソードずつ書く（視点の水増し。評価は 1 台）
- 7 関節より少ない腕（FANUC の 6 関節など）は joint_position の残りを 0 で埋め、モデルの出力の残りは使わない
- 行動の eef_9d は次の step の TCP の姿勢（記録に指令の TCP 姿勢はない。関節の指令に追従した結果）
- 各エピソードの末尾に最後のフレームを 39 回足す（hold_tail。グリッパを開く step も学習の標本にするため）
"""

from __future__ import annotations

import numpy as np

DROID_TAG = "OXE_DROID_RELATIVE_EEF_RELATIVE_JOINT"
DROID_TAG_VALUE = "oxe_droid_relative_eef_relative_joint"
DROID_MODEL = "nvidia/GR00T-N1.7-DROID"
DROID_JOINTS = 7
DROID_ACTION_HORIZON = 40  # DROID の embodiment の行動の長さ（delta_indices range(40)）
DROID_RESOLUTION = (180, 320)  # (高さ, 幅)。正方形の画像は左右を黒で埋める（tf.image.resize_with_pad と同じ）
# DROID の TCP の回転は panda_hand と同じ向き。Isaac Lab の TCP の回転行列にこれを掛ける（Isaac-GR00T の
# scripts/verify_droid_rotation_correction.py と同じ補正）
DROID_EEF_ROTATION_CORRECT = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=np.float64)
EXTERIOR_KEY = "exterior_image_1_left"
WRIST_KEY = "wrist_image_left"
LANGUAGE_KEY = "language.language_instruction"  # modality.json の annotation の下（列は annotation.language.language_instruction）
STATE_KEYS = (("eef_9d", 9), ("gripper_position", 1), ("joint_position", DROID_JOINTS))


def quat_xyzw_to_matrix(q: np.ndarray) -> np.ndarray:
    """(x, y, z, w) の四元数（Isaac Lab 3.0 の並び）→ 回転行列。(..., 4) → (..., 3, 3)。"""
    q = np.asarray(q, dtype=np.float64)
    x, y, z, w = np.moveaxis(q, -1, 0)
    s = 2.0 / np.sum(q * q, axis=-1)
    m = np.stack(
        [
            1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w),
            s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w),
            s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y),
        ],
        axis=-1,
    )  # fmt: skip
    return m.reshape(q.shape[:-1] + (3, 3))


def eef_9d(pos: np.ndarray, quat_xyzw: np.ndarray) -> np.ndarray:
    """DROID の eef_9d: 位置 3 + 補正した回転行列の上 2 行 6。(..., 3), (..., 4) → (..., 9)。"""
    r = quat_xyzw_to_matrix(quat_xyzw) @ DROID_EEF_ROTATION_CORRECT
    rows = r[..., :2, :].reshape(r.shape[:-2] + (6,))
    return np.concatenate([np.asarray(pos, dtype=np.float64), rows], axis=-1).astype(np.float32)


def pad_joints(q: np.ndarray, n: int = DROID_JOINTS) -> np.ndarray:
    """(..., k) の関節角を (..., n) にする（足りない分は 0。7 関節より多い腕は DROID に合わない）。"""
    q = np.asarray(q, dtype=np.float32)
    k = q.shape[-1]
    if k > n:
        raise ValueError(f"DROID の joint_position は {n} 関節です（この腕は {k} 関節）。")
    pad = np.zeros(q.shape[:-1] + (n - k,), dtype=np.float32)
    return np.concatenate([q, pad], axis=-1)


def gripper_closed(open_scalar: np.ndarray) -> np.ndarray:
    """urdf2vla のグリッパの値（1 = 開、0 = 閉）→ DROID の gripper_position（1 = 閉）。"""
    return (1.0 - np.asarray(open_scalar, dtype=np.float32)).astype(np.float32)


def resize_with_pad(img: np.ndarray, height: int = DROID_RESOLUTION[0], width: int = DROID_RESOLUTION[1]) -> np.ndarray:
    """縦横比を保って縮小・拡大し、余りを黒で埋める（DROID の client と同じ前処理）。"""
    from PIL import Image

    h, w = img.shape[:2]
    s = min(height / h, width / w)
    nh, nw = int(round(h * s)), int(round(w * s))
    out = np.zeros((height, width, 3), dtype=np.uint8)
    top, left = (height - nh) // 2, (width - nw) // 2
    out[top : top + nh, left : left + nw] = np.asarray(Image.fromarray(img).resize((nw, nh), Image.BILINEAR))
    return out


def episode_vectors(tcp_pos, tcp_quat, state, action, n_arm: int) -> tuple[np.ndarray, np.ndarray]:
    """1 エピソードの DROID の state / action（各 (T, 17)）。

    state / action は urdf2vla の単腕の並び [関節 n_arm..., グリッパ 1]（グリッパは 1 = 開）。
    行動の eef_9d は次の step の TCP の姿勢（最後の step は同じ姿勢のまま）。"""
    tcp_pos, tcp_quat = np.asarray(tcp_pos), np.asarray(tcp_quat)
    state, action = np.asarray(state, dtype=np.float32), np.asarray(action, dtype=np.float32)
    eef = eef_9d(tcp_pos, tcp_quat)
    eef_next = np.concatenate([eef[1:], eef[-1:]], axis=0)
    s = np.concatenate([eef, gripper_closed(state[:, n_arm : n_arm + 1]), pad_joints(state[:, :n_arm])], axis=1)
    a = np.concatenate([eef_next, gripper_closed(action[:, n_arm : n_arm + 1]), pad_joints(action[:, :n_arm])], axis=1)
    return s.astype(np.float32), a.astype(np.float32)


def single_arm_joints(vla) -> int:
    """DROID に合わせられる vla_interface（腕 1 本 7 関節以下 + グリッパ 1 つ）なら腕の関節数。合わなければ ValueError。"""
    arms = [g for g in vla.action.groups if g.kind == "joint_position"]
    grippers = [g for g in vla.action.groups if g.kind != "joint_position"]
    if len(arms) != 1 or len(grippers) != 1 or [g.name for g in vla.action.groups] != [arms[0].name, grippers[0].name]:
        raise ValueError("GR00T の DROID の embodiment は腕 1 本 + グリッパ 1 つのロボット向けです（双腕は --layout native）。")
    if arms[0].dim > DROID_JOINTS:
        raise ValueError(f"DROID の joint_position は {DROID_JOINTS} 関節までです（この腕は {arms[0].dim} 関節）。")
    return arms[0].dim


def pick_cameras(cameras: list[dict], image_cameras: list[str], exterior: list[str] | None = None) -> tuple[str, list[str]]:
    """DROID の手先カメラと外部カメラ（複数なら各デモをそれぞれで書く）。cameras は cameras.yaml の cameras。"""
    usable = [c for c in cameras if c["name"] in image_cameras]
    wrist = [c["name"] for c in usable if c.get("mount") == "link"]
    world = [c["name"] for c in usable if c.get("mount") == "world"]
    if len(wrist) != 1:
        raise ValueError(f"DROID には手先カメラ（mount: link）が 1 台要ります（記録にあるもの: {wrist}）。")
    if exterior:
        bad = [e for e in exterior if e not in world]
        if bad:
            raise ValueError(f"外部カメラ {bad} は記録した外部カメラ（mount: world）{world} にありません。")
        world = list(exterior)
    if not world:
        raise ValueError("DROID には外部カメラ（mount: world）が 1 台以上要ります。")
    return wrist[0], world


def modality_config_py() -> str:
    """学習・推論で import する modality config。GR00T に登録済みの DROID の設定を書き換える（登録し直すと assert で止まる）。

    動画は今のフレームだけ（delta [0]。nvidia/GR00T-N1.7-DROID と同じ）。事前学習の [-15, 0] は 15 Hz で 1 秒前の画像で、
    urdf2vla の制御周期（20 Hz）では意味が変わるうえ、Isaac-GR00T の学習はエピソードの先頭 15 step で負の添字が
    エピソードの末尾に回り込む（allow_padding が既定で False）。"""
    return f'''# urdf2vla が自動生成した GR00T N1.7 の modality config（DROID の embodiment。登録済みの設定の動画を delta [0] にする）
from gr00t.configs.data.embodiment_configs import MODALITY_CONFIGS
from gr00t.data.types import ModalityConfig

_droid = MODALITY_CONFIGS[{DROID_TAG_VALUE!r}]
_droid["video"] = ModalityConfig(delta_indices=[0], modality_keys=[{EXTERIOR_KEY!r}, {WRIST_KEY!r}])
'''


def hold_tail(n: int, horizon: int = DROID_ACTION_HORIZON) -> np.ndarray:
    """エピソードの末尾に最後のフレームを horizon - 1 回足す添字（urdf2vla.gr00t.episodes.hold_tail。理由もそこに書いてある）。"""
    from urdf2vla.gr00t.episodes import hold_tail as _hold

    return _hold(n, horizon)


def modality_json() -> dict:
    """DROID の embodiment の meta/modality.json（observation.state / action の範囲、動画、言語）。"""
    out: dict = {"state": {}, "action": {}, "video": {}, "annotation": {LANGUAGE_KEY: {"original_key": "task_index"}}}
    start = 0
    for name, dim in STATE_KEYS:
        out["state"][name] = {"start": start, "end": start + dim, "original_key": "observation.state"}
        out["action"][name] = {"start": start, "end": start + dim, "original_key": "action"}
        start += dim
    for key in (EXTERIOR_KEY, WRIST_KEY):
        out["video"][key] = {"original_key": f"observation.images.{key}"}
    return out
