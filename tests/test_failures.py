from urdf2vla.failures import HINTS, failure_reason, summarize


def test_failure_reasons():
    assert failure_reason("success", True, True, 0.2) is None
    assert failure_reason("timeout", False, True, 0.0) == "never_closed"
    assert failure_reason("timeout", True, False, 0.01) == "missed_grasp"
    assert failure_reason("timeout", True, False, 0.34) == "never_released"  # FANUC の最初の DROID の並びのモデル
    assert failure_reason("timeout", True, True, 0.30) == "released_off_target"


def test_summarize_picks_most_common_with_hint():
    s = summarize([None, "never_released", "never_released", "missed_grasp"])
    assert s["counts"] == {"never_released": 2, "missed_grasp": 1}
    assert s["most_common"] == "never_released" and s["hint"] == HINTS["never_released"]
    assert summarize([None, None]) == {"counts": {}}
