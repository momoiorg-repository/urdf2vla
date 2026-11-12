"""urdf2vla doctor（Isaac Sim・Docker 不要。コマンドの出力を差し替えて調べる）。"""

from __future__ import annotations

from pathlib import Path

from urdf2vla.doctor import check_docker, check_gpu, driver_hint, run_doctor
from urdf2vla.runtime.config import Mount, Runtime

SIM = Runtime(container="urdf2vla-sim", image="urdf2vla/isaaclab_arena:b9e37c5", mounts=[Mount(host="/repo", container="/workspace/urdf2vla")])


def fake(responses: dict):
    """コマンドの先頭の語（と 2 語目）→ (終了コード, stdout, stderr)。登録のないものは成功・出力なし。"""

    def run(cmd, timeout=30.0):
        for key in (" ".join(cmd[:3]), " ".join(cmd[:2]), cmd[0]):
            if key in responses:
                return responses[key]
        return 0, "", ""

    return run


def _modules(tmp_path: Path, with_nvidia: list[str], without: list[str]) -> Path:
    root = tmp_path / "modules"
    for k in with_nvidia:
        (root / k / "kernel" / "nvidia-580-open").mkdir(parents=True)
        (root / k / "kernel" / "nvidia-580-open" / "nvidia.ko").write_text("")
    for k in without:
        (root / k / "kernel").mkdir(parents=True)
    return root


def test_driver_hint_spots_a_kernel_update_without_nvidia_modules(tmp_path):
    root = _modules(tmp_path, ["6.17.0-22-generic"], ["7.0.0-42-generic"])
    dpkg = ("nvidia-driver-580-open install ok installed\n"
            "linux-modules-nvidia-580-open-generic-hwe-24.04 install ok installed\n"
            "linux-modules-nvidia-580-open-6.17.0-22-generic install ok installed\n")
    detail, fix = driver_hint("7.0.0-42-generic", root, fake({"dpkg-query": (1, dpkg, "")}))
    assert "7.0.0-42-generic" in detail and "6.17.0-22-generic" in detail
    assert "sudo apt install nvidia-driver-580-open linux-modules-nvidia-580-open-generic-hwe-24.04" in fix
    assert "6.17.0-22-generic" in fix  # 古いカーネルで起動する手もある


def test_driver_hint_without_a_newer_kernel_just_asks_for_a_driver(tmp_path):
    root = _modules(tmp_path, [], ["6.17.0-22-generic"])
    detail, fix = driver_hint("6.17.0-22-generic", root, fake({}))
    assert "nvidia-smi" in detail and "ドライバ" in fix


def test_gpu_and_docker_all_good():
    run = fake({
        "nvidia-smi --query-gpu=name,driver_version,memory.total": (0, "NVIDIA GeForce RTX 5080, 580.178.04, 16303\n", ""),
        "docker info --format": (0, '{"DockerRootDir": "/var/lib/docker"}', ""),
        "docker inspect -f": (0, "true\n", ""),
    })  # fmt: skip
    gpu = {c.name: c.level for c in check_gpu(run)}
    assert gpu["nvidia_driver"] == "ok" and gpu["gpu_memory"] == "ok" and gpu["gpu_training"] == "info"
    docker = {c.name: c.level for c in check_docker([SIM], run)}
    assert docker["docker"] == "ok" and docker["image:sim"] == "ok" and docker["container_gpu:sim"] == "ok" and docker["container_mount:sim"] == "ok"


def test_old_driver_is_a_warning_and_a_48gb_gpu_can_train():
    """クラウドの古いイメージ（例: L40S に 550）: 動くが、確かめた 580 より古いので警告。48 GB なら学習できる（info を出さない）。"""
    run = fake({"nvidia-smi --query-gpu=name,driver_version,memory.total": (0, "NVIDIA L40S, 550.127.05, 46068\n", "")})
    checks = {c.name: c for c in check_gpu(run)}
    assert checks["nvidia_driver"].level == "ok"
    assert checks["driver_version"].level == "warning" and "550.127.05" in checks["driver_version"].detail
    assert "nvidia-driver-580-open" in checks["driver_version"].fix
    assert "gpu_training" not in checks
    run = fake({"nvidia-smi --query-gpu=name,driver_version,memory.total": (0, "NVIDIA L40S, 580.95.05, 46068\n", "")})
    assert "driver_version" not in {c.name for c in check_gpu(run)}


def test_missing_image_and_docker_permission_are_errors_with_fixes():
    run = fake({"docker info --format": (0, "{}", ""), "docker image inspect": (1, "", "No such image")})
    c = next(c for c in check_docker([SIM], run) if c.name == "image:sim")
    assert c.level == "error" and "bash docker/build.sh" in c.fix
    run = fake({"docker info --format": (1, "", "permission denied while trying to connect to the Docker daemon socket")})
    (c,) = check_docker([SIM], run)
    assert c.level == "error" and "usermod -aG docker" in c.fix


def test_doctor_result_is_an_error_when_the_driver_is_gone(tmp_path):
    run = fake({"nvidia-smi --query-gpu=name,driver_version,memory.total": (9, "", "couldn't communicate with the NVIDIA driver"),
                "docker info --format": (0, "{}", "")})  # fmt: skip
    r = run_doctor(tmp_path, [SIM], run=run, assets=False)
    assert r.status == "error" and any(e.startswith("nvidia_driver:") for e in r.errors)
