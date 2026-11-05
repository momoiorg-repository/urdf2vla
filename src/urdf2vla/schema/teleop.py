"""Teleoperation と記録の設定。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TeleopConfig(BaseModel):
    device: Literal["scripted", "keyboard", "socket"] = "scripted"
    control: Literal["tcp_relative_ik"] = "tcp_relative_ik"  # 手先（TCP）の相対移動を IK で関節目標に変換
    pos_step: float = 0.008  # keyboard / socket: 1 制御 step あたりの移動量 [m]
    rot_step: float = 0.04  # 1 制御 step あたりの回転量 [rad]
    hold_steps: int = 3  # socket: 1 回のキー入力で動く step 数
    socket_port: int = 50555
    # scripted（自動操作）
    scripted_max_step: float = 0.008
    waypoint_tolerance: float = 0.006
    approach_height: float = 0.12  # 物体・置き場所の上で一度止まる高さ
    grasp_height_offset: float = 0.0  # 物体中心からの把持高さ
    place_height_offset: float = 0.012  # 置くときの高さ（物体の底が台に触れる少し上）
    gripper_wait_steps: int = 20  # 開閉を待つ step 数（グリッパの速度上限から自動計算）
    # 記録
    success_hold_steps: int = 10  # 成功状態がこの step 数続いたらデモを保存して終了
    max_episode_steps: int = 600
