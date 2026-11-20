"""docker/brev_setup.sh（NVIDIA Brev の準備）。root も GPU も要らない部分だけ: 構文、--help、--status の表示。
ドライバ・Docker・systemd の段階は、偽の nvidia-smi / docker / apt を置いた Ubuntu 22.04 のコンテナで確かめる（DEVELOPMENT.md）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "docker" / "brev_setup.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash がない")


def run(*args: str, state: Path | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}
    if state is not None:
        env["URDF2VLA_SETUP_STATE"] = str(state)
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)


def test_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_help_prints_the_header():
    r = run("--help")
    assert r.returncode == 0
    assert "NVIDIA Brev" in r.stdout and "HF_TOKEN" in r.stdout
    assert "set -euo" not in r.stdout


def test_unknown_argument_fails():
    assert run("--bogus").returncode == 2


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, "まだ始まっていません"),
        ("running: images", "セットアップ中（images）"),
        ("rebooting: driver", "再起動しています"),
        ("reboot needed: driver", "sudo reboot"),
        ("failed: cdi", "「cdi」で止まりました"),
        ("done", "セットアップ済み（/data/urdf2vla）"),
    ],
)
def test_status(tmp_path: Path, status: str | None, expected: str):
    if status is not None:
        (tmp_path / "status").write_text(status + "\n")
        (tmp_path / "env").write_text("TARGET_USER=ubuntu\nUSER_HOME=/home/ubuntu\nREPO=/data/urdf2vla\n")
    r = run("--status", state=tmp_path)
    assert r.returncode == 0
    assert expected in r.stdout


def test_status_done_shows_doctor_errors(tmp_path: Path):
    (tmp_path / "status").write_text("done\n")
    (tmp_path / "doctor.json").write_text('{"skill": "doctor", "status": "error", "errors": ["cdi_spec: GPU の CDI の定義がありません"]}')
    r = run("--status", state=tmp_path)
    assert "cdi_spec: GPU の CDI の定義がありません" in r.stdout
