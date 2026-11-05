import pytest
import yaml


@pytest.fixture(autouse=True)
def test_runtime(tmp_path, monkeypatch):
    """一時ディレクトリもコンテナにマウントされている想定の runtime.yaml を使う。"""
    rt = tmp_path / "runtime.yaml"
    rt.write_text(
        yaml.safe_dump(
            {
                "mode": "docker",
                "mounts": [
                    {"host": "${URDF2VLA_ROOT}", "container": "/workspace/urdf2vla"},
                    {"host": str(tmp_path), "container": "/tmpproj"},
                ],
            }
        )
    )
    monkeypatch.setenv("URDF2VLA_RUNTIME", str(rt))
