import numpy as np

from urdf2vla.gr00t.episodes import gripper_timing, hold_tail, last_change


def _grip(n: int, close_at: int, open_at: int) -> np.ndarray:
    g = np.ones(n)  # 1 = 開（urdf2vla の行動）
    g[close_at:open_at] = 0.0
    return g


def test_last_change_finds_release():
    assert last_change(_grip(166, 41, 153)) == 153
    assert last_change(np.ones(10)) is None


def test_release_13_steps_before_end_is_not_learnable_without_padding():
    # FANUC のスクリプトのデモ: 166 step、153 で開く。DROID の horizon 40 では標本は 0..126 から
    g = [_grip(166, 41, 153)]
    bad = gripper_timing(g, 40, padded=False)
    assert not bad["ok"] and bad["episodes_change_not_learnable"] == [0] and bad["min_steps_after_last_change"] == 12
    good = gripper_timing(g, 40, padded=True)
    assert good["ok"] and good["padded_frames"] == 39
    # urdf2vla の並び（horizon 16）は足さなくても、標本 150 の 3 番目に開くので学べる
    assert gripper_timing(g, 16, padded=False)["ok"]


def test_episode_without_gripper_change_is_reported():
    t = gripper_timing([np.ones(50), _grip(100, 10, 80)], 16, padded=True)
    assert t["ok"] and t["episodes_without_gripper_change"] == [0]


def test_release_step_for_both_gripper_conventions():
    from urdf2vla.gr00t.release_check import release_step

    native = _grip(166, 41, 153)  # 1 = 開
    assert release_step(native, open_is_high=True) == 153
    droid = (1.0 - native)[:, None]  # DROID は 1 = 閉
    assert release_step(droid, open_is_high=False) == 153
    assert release_step(np.zeros(20), open_is_high=True) is None


def test_hold_tail_lengths():
    idx = hold_tail(100, 16)
    assert len(idx) == 115 and idx[-1] == 99 and list(idx[:100]) == list(range(100))
    assert len(hold_tail(5, 1)) == 5
