"""シミュレーション状態の読み出し（TCP 姿勢・物体・VLA の state / action ラベル）。Isaac Sim 内でのみ使う。

四元数は (x, y, z, w)（Isaac Lab 3.0）。
"""

from __future__ import annotations

import torch

from urdf2vla.schema.robot import RobotConfig
from urdf2vla.schema.task import TaskConfig
from urdf2vla.schema.vla import VlaInterface
from urdf2vla.sim.lab import T, camera_rgb, env_origin


class SimView:
    def __init__(self, env, robot_cfg: RobotConfig, task: TaskConfig, vla: VlaInterface | None = None):
        self.env = env
        self.cfg = robot_cfg
        self.task = task
        self.vla = vla
        self.robot = env.scene["robot"]
        self.object = env.scene["object"]
        names = self.robot.joint_names
        self.device = env.device
        self.arm_ids = {a.name: [names.index(j) for j in a.joints] for a in robot_cfg.arms}
        self.body_ids = {a.name: self.robot.body_names.index(a.ik_body) for a in robot_cfg.arms}
        self.tcp_pos_b = {a.name: torch.tensor(a.tcp_offset.pos, device=self.device) for a in robot_cfg.arms}
        self.tcp_rot_b = {a.name: torch.tensor(a.tcp_offset.rot_xyzw, device=self.device) for a in robot_cfg.arms}
        self.grippers = {g.name: g for g in robot_cfg.grippers}
        self.leader_id = {g.name: names.index(g.leader) for g in robot_cfg.grippers}

    @property
    def origin(self) -> torch.Tensor:
        return env_origin(self.env)

    # ---- poses (env 座標) -----------------------------------------------------
    def tcp_pose(self, arm: str) -> tuple[torch.Tensor, torch.Tensor]:
        from isaaclab.utils.math import quat_apply, quat_mul

        bi = self.body_ids[arm]
        pos = T(self.robot.data.body_pos_w)[0, bi]
        quat = T(self.robot.data.body_quat_w)[0, bi]
        tcp = pos + quat_apply(quat.unsqueeze(0), self.tcp_pos_b[arm].unsqueeze(0))[0]
        q = quat_mul(quat.unsqueeze(0), self.tcp_rot_b[arm].unsqueeze(0))[0]
        return tcp - self.origin, q

    def tcp_pose_root(self, arm: str) -> tuple[torch.Tensor, torch.Tensor]:
        """ロボット base 座標での TCP 姿勢（IK の指令の座標系）。"""
        from isaaclab.utils.math import quat_apply_inverse, quat_conjugate, quat_mul

        p, q = self.tcp_pose(arm)
        root_p = T(self.robot.data.root_pos_w)[0] - self.origin
        root_q = self.root_quat()
        pr = quat_apply_inverse(root_q.unsqueeze(0), (p - root_p).unsqueeze(0))[0]
        qr = quat_mul(quat_conjugate(root_q.unsqueeze(0)), q.unsqueeze(0))[0]
        return pr, qr

    def root_quat(self) -> torch.Tensor:
        return T(self.robot.data.root_quat_w)[0]

    def object_pose(self) -> tuple[torch.Tensor, torch.Tensor]:
        return T(self.object.data.root_pos_w)[0] - self.origin, T(self.object.data.root_quat_w)[0]

    # ---- gripper ---------------------------------------------------------------
    def gripper_scalar(self, name: str, source: str = "state") -> float:
        """leader 関節を 0=閉 / 1=開 に正規化した値（dataset_scalar の定義）。"""
        g = self.grippers[name]
        i = self.leader_id[name]
        v = float(T(self.robot.data.joint_pos)[0, i] if source == "state" else T(self.robot.data.joint_pos_target)[0, i])
        lo, hi = g.close[g.leader], g.open[g.leader]
        if abs(hi - lo) < 1e-9:
            return 1.0
        return float(min(max((v - lo) / (hi - lo), 0.0), 1.0))

    # ---- VLA の state / action -------------------------------------------------
    def vla_vector(self, source: str) -> list[float]:
        """source="state": 現在の関節角 / "target": 直前の env.step で IK が出した関節目標（行動ラベル）。"""
        data = T(self.robot.data.joint_pos) if source == "state" else T(self.robot.data.joint_pos_target)
        names = self.robot.joint_names
        out: list[float] = []
        spec = self.vla.state if source == "state" else self.vla.action
        for g in spec.groups:
            if g.kind == "joint_position":
                out += [float(data[0, names.index(j)]) for j in g.joints]
            else:
                out.append(self.gripper_scalar(g.gripper, "state" if source == "state" else "target"))
        return out

    # ---- success ---------------------------------------------------------------
    def task_success(self, gripper_open: bool) -> bool:
        pos, _ = self.object_pose()
        tx, ty = self.task.target.pos
        d = float(torch.linalg.norm(pos[:2] - torch.tensor([tx, ty], device=self.device)))
        resting = float(pos[2]) < self.task.table.top_z + self.task.object.size[2] / 2 + self.task.success.max_height_above_table
        released = gripper_open or not self.task.success.require_release
        return d < self.task.success.radius and resting and released

    def images(self, names: list[str]) -> dict[str, torch.Tensor]:
        return {n: camera_rgb(self.env, n) for n in names}

    def joint_pos(self) -> torch.Tensor:
        """現在の関節角（env 0, sim の関節順）。"""
        return T(self.robot.data.joint_pos)[0]

    def set_gripper_targets(self, target: torch.Tensor, name: str, opened: bool) -> None:
        """関節目標 target（1×関節数）のうち、グリッパ name の全関節を開 / 閉の値にする。"""
        g = self.grippers[name]
        names = self.robot.joint_names
        for j in g.joints:
            target[0, names.index(j)] = (g.open if opened else g.close)[j]
