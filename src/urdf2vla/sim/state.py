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
    def __init__(self, env, robot_cfg: RobotConfig, task: TaskConfig, vla: VlaInterface | None = None, project_root=None):
        self.env = env
        self.cfg = robot_cfg
        self.task = task
        self.vla = vla
        self.robot = env.scene["robot"]
        # 物体の候補（エピソードごとに 1 つを台に置き、残りは台から離れた所に置いたまま）
        self.objects = [env.scene[f"object_{i}"] for i in range(len(task.objects))]
        self.active = 0
        names = self.robot.joint_names
        self.device = env.device
        self.arm_ids = {a.name: [names.index(j) for j in a.joints] for a in robot_cfg.arms}
        self.body_ids = {a.name: self.robot.body_names.index(a.ik_body) for a in robot_cfg.arms}
        self.tcp_pos_b = {a.name: torch.tensor(a.tcp_offset.pos, device=self.device) for a in robot_cfg.arms}
        self.tcp_rot_b = {a.name: torch.tensor(a.tcp_offset.rot_xyzw, device=self.device) for a in robot_cfg.arms}
        self.grippers = {g.name: g for g in robot_cfg.grippers}
        self.leader_id = {g.name: names.index(g.leader) for g in robot_cfg.grippers}
        self._measure_objects()
        self.rest = {}
        if project_root is not None:
            import json
            from pathlib import Path

            f = Path(project_root) / "build" / "object_rest.json"
            if f.exists():
                self.set_rest_poses(json.loads(f.read_text()))

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

    # ---- 物体 -------------------------------------------------------------------
    @property
    def object(self):
        """このエピソードで台に置いた物体。"""
        return self.objects[self.active]

    @property
    def object_cfg(self):
        return self.task.objects[self.active]

    def instruction(self) -> str:
        return self.task.instruction_for(self.active)

    def object_pose(self) -> tuple[torch.Tensor, torch.Tensor]:
        return T(self.object.data.root_pos_w)[0] - self.origin, T(self.object.data.root_quat_w)[0]

    def _measure_objects(self) -> None:
        """物体の候補ごとに、剛体の原点から見た外接箱の中心・底と、生成時の剛体の向きを USD から測る。

        アセットによって剛体の原点の場所（底・中心・別の点）や向き（Y-up のメッシュを回したもの）が違うので、
        カタログの値ではなく生成した物から測る。オフセットは生成時の向き（直立）の world 座標で持つ。
        """
        from pxr import Usd, UsdGeom, UsdPhysics

        import isaaclab.sim as sim_utils

        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"], useExtentsHint=False)
        self.center_off0, self.bottom0, self.quat0 = [], [], []
        for obj in self.objects:
            root = sim_utils.find_matching_prims(obj.cfg.prim_path)[0]
            body = next((p for p in Usd.PrimRange(root) if p.HasAPI(UsdPhysics.RigidBodyAPI)), root)
            box = cache.ComputeWorldBound(root).ComputeAlignedRange()
            m = UsdGeom.Xformable(body).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            t = m.ExtractTranslation()
            c = (box.GetMin() + box.GetMax()) / 2
            self.center_off0.append(torch.tensor([c[k] - t[k] for k in range(3)], device=self.device, dtype=torch.float32))
            self.bottom0.append(float(box.GetMin()[2] - t[2]))
            q = m.RemoveScaleShear().ExtractRotationQuat()
            im = q.GetImaginary()
            self.quat0.append(torch.tensor([im[0], im[1], im[2], q.GetReal()], device=self.device, dtype=torch.float32))

    def set_rest_poses(self, rest: dict) -> None:
        """launch_sim が測った、台の上で落ち着く向き（丸い物体は直立では倒れる）。{key: {"quat_xyzw", "root_height"}}。"""
        self.rest = {}
        for i, o in enumerate(self.task.objects):
            if o.key in rest:
                r = rest[o.key]
                self.rest[i] = (torch.tensor(r["quat_xyzw"], device=self.device, dtype=torch.float32), float(r["root_height"]))

    def upright_quat(self, index: int, yaw: float) -> torch.Tensor:
        """物体 index を、台の上で落ち着く向き（測っていなければ生成時の向き = 直立）から鉛直軸まわりに yaw 回した向き (x, y, z, w)。"""
        import math

        from isaaclab.utils.math import quat_mul

        base = self.rest[index][0] if index in getattr(self, "rest", {}) else self.quat0[index]
        qz = torch.tensor([[0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]], device=self.device)
        return quat_mul(qz, base.unsqueeze(0))[0]

    def pose_on_table(self, index: int, x: float, y: float, yaw: float = 0.0) -> list[float]:
        """物体 index を台の上 (x, y) に、落ち着く向きで置く剛体の姿勢（env 座標の [x, y, z, qx, qy, qz, qw]）。"""
        if index in getattr(self, "rest", {}):
            z = self.task.table.top_z + self.rest[index][1] + 0.003
        else:
            z = self.task.table.top_z - self.bottom0[index] + 0.003
        return [x, y, z, *self.upright_quat(index, yaw).tolist()]

    def center_offset(self, index: int, quat: torch.Tensor) -> torch.Tensor:
        """剛体の向きが quat のときの、剛体の原点から外接箱の中心へのベクトル（world 座標）。"""
        from isaaclab.utils.math import quat_apply, quat_conjugate, quat_mul

        rel = quat_mul(quat.unsqueeze(0), quat_conjugate(self.quat0[index].unsqueeze(0)))
        return quat_apply(rel, self.center_off0[index].unsqueeze(0))[0]

    def object_center(self) -> torch.Tensor:
        """物体の外接箱の中心（env 座標）。剛体の原点はアセットによって底や別の点にあるので、原点ではなくこれを使う。"""
        p, q = self.object_pose()
        return p + self.center_offset(self.active, q)

    def sample_episode(self) -> tuple[int, list[float]]:
        """物体の候補から 1 つと、台の上の置き方（位置と向き。env 座標の [x, y, z, qx, qy, qz, qw]）を選ぶ（torch の乱数）。"""
        i = int(torch.randint(len(self.objects), (1,)))
        t = self.task
        x = t.spawn_x[0] + float(torch.rand(1)) * (t.spawn_x[1] - t.spawn_x[0])
        y = t.spawn_y[0] + float(torch.rand(1)) * (t.spawn_y[1] - t.spawn_y[0])
        yaw = (float(torch.rand(1)) * 2 - 1) * 0.5
        return i, self.pose_on_table(i, x, y, yaw)

    def replay_object(self, index: int, pose: list[float]) -> None:
        """記録した初期姿勢（台の上で落ち着いた姿勢）に物体を置き直す。接した状態のまま置くと、衝突形状のめり込みを解く力で
        丸い物体が転がり出す（りんごが 10 cm 転がった）ので、記録のときと同じく 3 mm 上から落として落ち着かせる。"""
        p = list(pose)
        p[2] += 0.003
        self.place_object(index, p)

    def place_object(self, index: int, pose: list[float], settle_steps: int = 15) -> None:
        """物体 index を pose（env 座標）に置き、このエピソードの物体にする。

        物理を settle_steps 進めて台の上に落ち着かせ（丸い物体は置いた直後に少し転がる。落ち着いた後の姿勢をエピソードの
        初期状態として記録すれば、再生で同じ場所から始まる）、最後の step で描画して最初の画像に写るようにする。
        """
        from urdf2vla.sim.lab import write_root_pose

        self.active = index
        p = torch.tensor([pose], device=self.device, dtype=torch.float32)
        p[:, :3] += self.origin
        write_root_pose(self.object, p)
        self.env.scene.write_data_to_sim()
        for k in range(max(settle_steps, 1)):
            self.env.sim.step(render=k == max(settle_steps, 1) - 1)
            self.env.scene.update(self.env.physics_dt)

    def start_episode(self) -> tuple[int, list[float]]:
        """env.reset() の後に呼ぶ: 物体を選んで台に置く。選んだ物体の番号と、置いた姿勢（落ち着かせる前）を返す。

        再生では同じ姿勢から同じ手順（place_object）で置き直すと、落ち着いた後の状態まで記録と同じになる
        （落ち着いた後の姿勢から置き直すと、丸い物体は少し違う転がり方をして、開ループの再生で指が外れた）。
        """
        i, pose = self.sample_episode()
        self.place_object(i, pose)
        return i, pose

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
        pos = self.object_center()
        tx, ty = self.task.target.pos
        t, sc, obj = self.task.target, self.task.success, self.object_cfg
        released = gripper_open or not sc.require_release
        if t.is_container:
            # 容器: 物体の中心が容器の縁より内側にあり、容器の中に置かれている（持ち上げ中でない）
            inside = abs(float(pos[0]) - tx) < t.size[0] / 2 - sc.container_margin and abs(float(pos[1]) - ty) < t.size[1] / 2 - sc.container_margin
            resting = float(pos[2]) < self.task.table.top_z + t.size[2] + obj.size[2] / 2 + sc.max_height_above_table
            return inside and resting and released
        d = float(torch.linalg.norm(pos[:2] - torch.tensor([tx, ty], device=self.device)))
        resting = float(pos[2]) < self.task.table.top_z + obj.size[2] / 2 + sc.max_height_above_table
        return d < sc.radius and resting and released

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
