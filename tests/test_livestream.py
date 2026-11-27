import json
import os
import sys
from types import ModuleType

import pytest

from urdf2vla import cli
from urdf2vla.sim import entry


@pytest.mark.parametrize(
    ("skill", "options", "mode", "address"),
    [
        ("launch_sim", [], None, None),
        ("launch_sim", ["--livestream"], 2, None),
        ("launch_sim", ["--livestream", "1", "--public-ip", "203.0.113.5"], 1, "203.0.113.5"),
        ("collect_dataset", ["--livestream"], 2, None),
    ],
)
def test_sim_entry_passes_livestream_configuration_to_kit(monkeypatch, skill, options, mode, address):
    class ReachedKit(Exception):
        pass

    def launch_simulation(cfg, launcher):
        assert launcher.get("livestream") == mode
        assert os.environ.get("PUBLIC_IP") == address
        raise ReachedKit

    isaaclab_app = ModuleType("isaaclab.app")
    isaaclab_app.launch_simulation = launch_simulation
    monkeypatch.setitem(sys.modules, "isaaclab.app", isaaclab_app)
    monkeypatch.setattr(os, "environ", {key: value for key, value in os.environ.items() if key != "PUBLIC_IP"})
    monkeypatch.setattr(sys, "argv", ["sim.entry", skill, "--project", "demo", *options])
    with pytest.raises(ReachedKit):
        entry.main()


@pytest.mark.parametrize("options", [["--livestream", "1"], ["--public-ip", "203.0.113.5"]])
def test_public_mode_and_ipv4_are_required_together(capsys, options):
    assert cli.main(["launch_sim", *options]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "--public-ip" in result["errors"][0]


@pytest.mark.parametrize("address", ["brev-host.example", "203.0.113.256", "2001:db8::1"])
def test_public_ip_must_be_ipv4_and_fails_as_json(capsys, address):
    assert cli.main(["launch_sim", "--livestream", "1", "--public-ip", address]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "IPv4" in result["errors"][0] and address in result["errors"][0]
