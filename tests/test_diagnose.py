import pytest

from urdf2vla.diagnose import diagnose_text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("シミュレーション用コンテナ urdf2vla-sim が起動していません。", "container_down"),
        ("X Error of failed request:  GLXBadFBConfig", "glx_fbconfig"),
        ("検査 default_pose_tracking に失敗しました: {'max_error': 0.3}", "tracking"),
        ("[urdf2vla] check camera_alive:wrist_cam: FAIL {'frozen_steps': 59}", "camera_frozen"),
        ("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate", "cuda_oom"),
        ("import_robot が 900 秒以内に終わりませんでした。", "timeout"),
        ("[Error] Failed to open mesh file /x/link1.stl", "mesh_missing"),
    ],
)
def test_known_patterns(text, expected):
    ids = [f["id"] for f in diagnose_text(text)]
    assert expected in ids


def test_no_false_positive_on_normal_log():
    log = "[urdf2vla] check no_nan: OK 関節角が有限\n[INFO] Simulation App Startup Complete\nnanoseconds elapsed"
    assert diagnose_text(log) == []
