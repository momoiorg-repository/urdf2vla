"""GR00T 推論サーバ（gr00t/eval/run_gr00t_server.py）のクライアント。

Isaac Sim の python には gr00t を入れないので、サーバの通信形式（ZeroMQ REQ/REP + msgpack、
numpy 配列は npy バイト列）だけを実装する。
"""

from __future__ import annotations

import io
import time
from typing import Any

import msgpack
import numpy as np
import zmq


def _encode(obj):
    if isinstance(obj, np.ndarray):
        buf = io.BytesIO()
        np.save(buf, obj, allow_pickle=False)
        return {"__ndarray_class__": True, "as_npy": buf.getvalue()}
    return obj


def _decode(obj):
    if isinstance(obj, dict) and "__ndarray_class__" in obj:
        return np.load(io.BytesIO(obj["as_npy"]), allow_pickle=False)
    return obj


class Gr00tClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 5555, timeout_ms: int = 30000):
        self.host, self.port, self.timeout_ms = host, port, timeout_ms
        self.ctx = zmq.Context()
        self._connect()

    def _connect(self) -> None:
        self.sock = self.ctx.socket(zmq.REQ)
        self.sock.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
        self.sock.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.connect(f"tcp://{self.host}:{self.port}")

    def call(self, endpoint: str, data: dict | None = None) -> Any:
        req: dict = {"endpoint": endpoint}
        if data is not None:
            req["data"] = data
        try:
            self.sock.send(msgpack.packb(req, default=_encode))
            msg = self.sock.recv()
        except zmq.error.Again:
            self.sock.close()
            self._connect()
            raise
        if msg == b"ERROR":
            raise RuntimeError("GR00T サーバがエラーを返しました（サーバのログを確認してください）")
        resp = msgpack.unpackb(msg, object_hook=_decode)
        if isinstance(resp, dict) and "error" in resp:
            raise RuntimeError(f"GR00T サーバのエラー: {resp['error']}")
        return resp

    def wait_ready(self, timeout_s: float = 300.0) -> bool:
        end = time.time() + timeout_s
        old = self.timeout_ms
        self.sock.setsockopt(zmq.RCVTIMEO, 2000)
        try:
            while time.time() < end:
                try:
                    self.call("ping")
                    return True
                except zmq.error.Again:
                    self.sock.setsockopt(zmq.RCVTIMEO, 2000)
            return False
        finally:
            self.sock.setsockopt(zmq.RCVTIMEO, old)

    def reset(self, options: dict | None = None) -> Any:
        return self.call("reset", {"options": options})

    def get_action(self, observation: dict) -> dict[str, np.ndarray]:
        resp = self.call("get_action", {"observation": observation, "options": None})
        action = resp[0] if isinstance(resp, (list, tuple)) else resp
        return {k.removeprefix("action."): np.asarray(v) for k, v in action.items()}
