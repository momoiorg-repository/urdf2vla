import subprocess
import sys


def test_cli_and_skill_registry_import_without_numpy():
    """docs のビルド（CI）は pip install -e '.[docs]' だけで、numpy がない。Skill の登録（gen_reference.py）が動くこと。"""
    code = "import sys; sys.modules['numpy'] = None; from urdf2vla.cli import build_parser; build_parser()"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
