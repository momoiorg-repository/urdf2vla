"""ポートが空いているか（コンテナは host network なので、ホストの 127.0.0.1 で確かめる）。doctor と evaluate_vla が使う。"""

from __future__ import annotations

import socket


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def free_port(port: int, tries: int = 50) -> int:
    """port から順に、空いている最初のポート（見つからなければ port のまま）。"""
    return next((p for p in range(port, port + tries) if port_free(p)), port)
