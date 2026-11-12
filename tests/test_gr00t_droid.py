import numpy as np
import pytest

from urdf2vla.gr00t import droid


def test_quat_xyzw_to_matrix_matches_known_rotations():
    assert np.allclose(droid.quat_xyzw_to_matrix([0, 0, 0, 1]), np.eye(3))
    s = np.sqrt(0.5)  # z 軸まわりに 90°: x → y
    r = droid.quat_xyzw_to_matrix([0, 0, s, s])
    assert np.allclose(r @ [1, 0, 0], [0, 1, 0])
    batch = droid.quat_xyzw_to_matrix(np.tile([0, 0, s, s], (5, 1)))
    assert batch.shape == (5, 3, 3)


def test_eef_9d_is_position_plus_two_rows_of_corrected_rotation():
    v = droid.eef_9d([0.1, 0.2, 0.3], [0, 0, 0, 1])
    assert v.shape == (9,) and v.dtype == np.float32
    assert np.allclose(v[:3], [0.1, 0.2, 0.3])
    assert np.allclose(v[3:], droid.DROID_EEF_ROTATION_CORRECT[:2].reshape(6))


def test_pad_joints_fills_with_zero_and_rejects_more_than_seven():
    q = droid.pad_joints(np.ones((4, 6)))
    assert q.shape == (4, 7) and np.all(q[:, 6] == 0) and np.all(q[:, :6] == 1)
    assert droid.pad_joints(np.ones(7)).shape == (7,)
    with pytest.raises(ValueError):
        droid.pad_joints(np.ones(8))


def test_episode_vectors_layout_for_six_joint_arm():
    T = 5
    pos = np.stack([np.linspace(0, 0.4, T), np.zeros(T), np.full(T, 0.3)], axis=1)
    quat = np.tile([0, 0, 0, 1.0], (T, 1))
    state = np.concatenate([np.full((T, 6), 0.5), np.ones((T, 1))], axis=1)  # グリッパ 1 = 開
    action = np.concatenate([np.full((T, 6), 0.6), np.zeros((T, 1))], axis=1)  # 0 = 閉める
    s, a = droid.episode_vectors(pos, quat, state, action, n_arm=6)
    assert s.shape == a.shape == (T, 17)
    assert np.allclose(s[:, 9], 0.0) and np.allclose(a[:, 9], 1.0)  # DROID は 1 = 閉
    assert np.allclose(s[:, 10:16], 0.5) and np.allclose(s[:, 16], 0.0)  # 7 つ目の関節は 0
    assert np.allclose(a[:-1, :3], s[1:, :3]) and np.allclose(a[-1, :3], s[-1, :3])  # 行動の eef は次の step


def test_modality_json_matches_droid_embodiment_keys():
    m = droid.modality_json()
    assert list(m["state"]) == ["eef_9d", "gripper_position", "joint_position"]
    assert m["state"]["joint_position"] == {"start": 10, "end": 17, "original_key": "observation.state"}
    assert m["action"]["eef_9d"]["end"] == 9
    assert set(m["video"]) == {droid.EXTERIOR_KEY, droid.WRIST_KEY}
    assert m["annotation"] == {"language.language_instruction": {"original_key": "task_index"}}


def test_resize_with_pad_keeps_aspect():
    img = np.full((256, 256, 3), 255, dtype=np.uint8)
    out = droid.resize_with_pad(img)
    assert out.shape == (180, 320, 3)
    assert out[:, :60].max() == 0 and out[:, 70:250].min() == 255  # 左右は黒
