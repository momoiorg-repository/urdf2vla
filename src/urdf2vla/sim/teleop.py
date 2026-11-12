"""teleop デバイス（Isaac Sim 内）。

デバイスは「arm ごとの TCP の相対移動（keyboard / socket）」か「arm ごとの目標 TCP 姿勢（scripted）」を出し、
TargetIntegrator がそれを目標手先姿勢に積算して IK（絶対姿勢）への行動にする。
腕の追従が遅れても指令した位置に到達し、目標が実際の手先から離れすぎないように制限する。

- scripted: 物体と置き場所の位置を知っている自動操作（人がいなくてもデータ収集〜学習を通して試せる）
- keyboard: Isaac Sim のキーボード。GUI または WebRTC 配信のウィンドウにフォーカスして操作
- socket:   ホストの端末から `urdf2vla teleop-keys` で送るキー入力（GUI 不要）

キー割り当て（keyboard / socket 共通）:
  W/S: x  A/D: y  Q/E: z  Z/X: roll  T/G: pitch  C/V: yaw  K: グリッパ開閉  N: 操作する arm を切替  R: やり直し  ESC: 終了
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field

import torch

from urdf2vla.schema.teleop import TeleopConfig
from urdf2vla.sim.lab import T
from urdf2vla.sim.state import SimView


@dataclass
class Command:
    arm_delta: dict[str, list[float]] = field(default_factory=dict)  # arm -> [dx,dy,dz, rx,ry,rz]（base 座標）
    arm_goal: dict[str, tuple[torch.Tensor, torch.Tensor]] = field(default_factory=dict)  # arm -> (pos, quat)（base 座標）
    gripper_open: dict[str, bool] = field(default_factory=dict)
    reset: bool = False
    quit: bool = False
    done: bool = False  # scripted: 動作を終えた
    raw: torch.Tensor | None = None  # 環境の行動をそのまま使う（Isaac Lab Mimic で生成したエピソードの再生）


def _rotvec(q: torch.Tensor) -> torch.Tensor:
    """クォータニオン (x, y, z, w) → 回転ベクトル。"""
    q = q if q[3] >= 0 else -q
    v = q[:3]
    s = torch.linalg.norm(v)
    if s < 1e-9:
        return torch.zeros(3, device=q.device)
    return v / s * 2 * torch.atan2(s, q[3])


def _quat_from_rotvec(r: torch.Tensor) -> torch.Tensor:
    from isaaclab.utils.math import quat_from_angle_axis

    angle = torch.linalg.norm(r)
    if angle < 1e-9:
        return torch.tensor([0.0, 0.0, 0.0, 1.0], device=r.device)  # (x, y, z, w) の単位
    return quat_from_angle_axis(angle.unsqueeze(0), (r / angle).unsqueeze(0))[0]


class TargetIntegrator:
    """デバイスの指令 → teleop 環境の行動（arm ごとに base 座標の目標 TCP 姿勢 7 次元, gripper ごとに ±1）。"""

    def __init__(self, cfg: TeleopConfig, view: SimView, max_lead: float = 0.04):
        self.cfg = cfg
        self.max_lead = max_lead
        self.arms = [a.name for a in view.cfg.arms]
        self.pos: dict[str, torch.Tensor] = {}
        self.quat: dict[str, torch.Tensor] = {}

    def reset(self, view: SimView) -> None:
        for arm in self.arms:
            p, q = view.tcp_pose_root(arm)
            self.pos[arm], self.quat[arm] = p.clone(), q.clone()

    def action(self, cmd: Command, view: SimView, max_step: float) -> torch.Tensor:
        from isaaclab.utils.math import quat_conjugate, quat_mul

        vals: list[torch.Tensor] = []
        for arm in self.arms:
            if arm in cmd.arm_goal:
                gp, gq = cmd.arm_goal[arm]
                dp = gp - self.pos[arm]
                n = float(torch.linalg.norm(dp))
                if n > max_step:
                    dp = dp / n * max_step
                dr = _rotvec(quat_mul(gq.unsqueeze(0), quat_conjugate(self.quat[arm].unsqueeze(0)))[0])
            else:
                d = torch.tensor(cmd.arm_delta.get(arm, [0.0] * 6), device=view.device)
                dp, dr = d[:3], d[3:]
            rn = float(torch.linalg.norm(dr))
            if rn > self.cfg.rot_step:
                dr = dr / rn * self.cfg.rot_step
            self.pos[arm] = self.pos[arm] + dp
            self.quat[arm] = quat_mul(_quat_from_rotvec(dr).unsqueeze(0), self.quat[arm].unsqueeze(0))[0]
            self.quat[arm] = self.quat[arm] / torch.linalg.norm(self.quat[arm])
            # 目標が実際の手先から離れすぎない（ぶつかったときに IK が暴れない）
            cur, _ = view.tcp_pose_root(arm)
            lead = self.pos[arm] - cur
            ln = float(torch.linalg.norm(lead))
            if ln > self.max_lead:
                self.pos[arm] = cur + lead / ln * self.max_lead
            vals += [self.pos[arm], self.quat[arm]]
        grip = [1.0 if cmd.gripper_open.get(g.name, True) else -1.0 for g in view.cfg.grippers]
        vals.append(torch.tensor(grip, device=view.device))
        return torch.cat(vals).unsqueeze(0)


class TeleopDevice:
    name = "base"

    def reset(self, view: SimView) -> None: ...

    def advance(self, view: SimView, integ: TargetIntegrator) -> Command:
        raise NotImplementedError


# --------------------------------------------------------------------------- scripted


class ScriptedExpert(TeleopDevice):
    """物体の真上 → 下降 → 把持 → 持ち上げ → 置き場所の上 → 下降 → 解放 → 退避（物体に近い方の arm を使う）。
    物体の位置はシミュレータから直接読む（特権情報）。手先の接近方向は真下に保つ（鉛直軸まわりは自由）。"""

    name = "scripted"

    def __init__(self, cfg: TeleopConfig, view: SimView):
        self.cfg = cfg
        self._select_arm(view, view.cfg.arms[0].name)

    def _select_arm(self, view: SimView, arm: str) -> None:
        self.arm = arm
        g = view.cfg.gripper_for(arm)
        self.gripper = g.name if g else None

    def reset(self, view: SimView) -> None:
        # 双腕なら物体に近い方の手で掴む
        obj = view.object_center()
        dist = {a.name: float(torch.linalg.norm(view.tcp_pose(a.name)[0][:2] - obj[:2])) for a in view.cfg.arms}
        self._select_arm(view, min(dist, key=dist.get))
        self.phase = 0
        self.wait = 0
        self.open = True
        self.grasp_latched = None
        self.half_latched = None

    def _down_orientation(self, view: SimView, q_tcp: torch.Tensor) -> torch.Tensor:
        """接近方向だけを真下に合わせた手先の向き（鉛直軸まわりの回転は今のまま）。
        向きを完全に固定すると、小さな腕では届く位置でも IK が止まることがあるため。"""
        from isaaclab.utils.math import quat_apply, quat_apply_inverse, quat_from_angle_axis, quat_mul

        arm = next(a for a in view.cfg.arms if a.name == self.arm)
        a_body = torch.tensor(arm.approach_axis, device=view.device)
        a_tcp = quat_apply_inverse(view.tcp_rot_b[self.arm].unsqueeze(0), a_body.unsqueeze(0))[0]
        cur = quat_apply(q_tcp.unsqueeze(0), a_tcp.unsqueeze(0))[0]
        down = quat_apply_inverse(view.root_quat().unsqueeze(0), torch.tensor([[0.0, 0.0, -1.0]], device=view.device))[0]
        axis = torch.linalg.cross(cur, down)
        s, c = float(torch.linalg.norm(axis)), float(torch.dot(cur, down))
        if s < 1e-6:
            return q_tcp
        dq = quat_from_angle_axis(torch.tensor([float(torch.atan2(torch.tensor(s), torch.tensor(c)))], device=view.device), (axis / s).unsqueeze(0))[0]
        return quat_mul(dq.unsqueeze(0), q_tcp.unsqueeze(0))[0]

    def _grasp_orientation(self, view: SimView, q_tcp: torch.Tensor) -> torch.Tensor:
        """接近方向は真下。掴む前は、指の開閉方向を物体の面に垂直にそろえる（鉛直軸まわりに回す）。
        掴んだ後は回さない（持っている物体を回さないため）。"""
        import math

        from isaaclab.utils.math import quat_apply, quat_apply_inverse, quat_from_angle_axis, quat_mul

        q = self._down_orientation(view, q_tcp)
        arm = next(a for a in view.cfg.arms if a.name == self.arm)
        if self.phase >= 2 or not arm.finger_axis:
            return q
        f_tcp = quat_apply_inverse(view.tcp_rot_b[self.arm].unsqueeze(0), torch.tensor([arm.finger_axis], device=view.device))
        f = quat_apply(q.unsqueeze(0), f_tcp)[0]
        _, obj_q = view.object_pose()
        # 指の開閉方向を物体の水平の短い辺にそろえる。縦横がほぼ同じ（立方体・丸い果物）なら 90° ごとに同じなので近い面に、
        # 細長い物体（洋梨など）は 180° ごと（短い辺の向きに限る）
        sx, sy = view.object_cfg.size[0], view.object_cfg.size[1]
        short = [1.0, 0.0, 0.0] if sx <= sy else [0.0, 1.0, 0.0]
        period = math.pi / 2 if max(sx, sy) / max(min(sx, sy), 1e-6) < 1.2 else math.pi
        ox = quat_apply(obj_q.unsqueeze(0), torch.tensor([short], device=view.device))
        ox = quat_apply_inverse(view.root_quat().unsqueeze(0), ox)[0]
        a = math.atan2(float(f[0] * ox[1] - f[1] * ox[0]), float(f[0] * ox[0] + f[1] * ox[1]))
        a = (a + period / 2) % period - period / 2
        if abs(a) < 1e-3:
            return q
        dq = quat_from_angle_axis(torch.tensor([a], device=view.device), torch.tensor([[0.0, 0.0, 1.0]], device=view.device))[0]
        return quat_mul(dq.unsqueeze(0), q.unsqueeze(0))[0]

    def _to_root(self, view: SimView, p_env: torch.Tensor) -> torch.Tensor:
        from isaaclab.utils.math import quat_apply_inverse

        root_p = T(view.robot.data.root_pos_w)[0] - view.origin
        return quat_apply_inverse(view.root_quat().unsqueeze(0), (p_env - root_p).unsqueeze(0))[0]

    def advance(self, view: SimView, integ: TargetIntegrator) -> Command:
        cmd = Command()
        c = self.cfg
        obj = view.object_center()  # 外接箱の中心（剛体の原点はアセットによって底や別の点にある）
        tx, ty = view.task.target.pos
        top = view.task.table.top_z
        # 掴む高さは物体の実際の中心の高さ（丸い物体は倒れた向きで置くので、直立の高さの半分ではない）
        grasp = self._to_root(view, torch.tensor([float(obj[0]), float(obj[1]), float(obj[2]) + c.grasp_height_offset], device=view.device))
        # 把持を始めたら把持位置と、置いたときの中心の高さを固定する（つかんだ物体は手と一緒に動くので、追いかけ続けないように）
        if self.phase >= 2:
            if self.grasp_latched is None:
                self.grasp_latched = grasp.clone()
                self.half_latched = float(obj[2]) - top
            grasp = self.grasp_latched
        half = self.half_latched if self.phase >= 2 and self.half_latched is not None else float(obj[2]) - top
        # 置く高さ: 容器なら容器の縁の上（そこで離して中に落とす）、円なら台の上
        floor = top + (view.task.target.size[2] if view.task.target.is_container else 0.0)
        place = self._to_root(view, torch.tensor([tx, ty, floor + half + c.place_height_offset], device=view.device))
        up = torch.tensor([0.0, 0.0, c.approach_height], device=view.device)
        plan = [("move", grasp + up), ("move", grasp), ("close", None), ("move", grasp + up),
                ("move", place + up), ("move", place), ("open", None), ("move", place + up)]  # fmt: skip
        pos, _ = view.tcp_pose_root(self.arm)
        goal = integ.pos[self.arm]
        if self.phase >= len(plan):
            cmd.done = True
        else:
            kind, target = plan[self.phase]
            if kind in ("close", "open"):
                self.open = kind == "open"
                self.wait += 1
                if self.wait >= c.gripper_wait_steps:
                    self.wait, self.phase = 0, self.phase + 1
            else:
                goal = target
                # 物体・トレイの真上（phase 0, 4）と、掴む・置く高さへの下降（phase 1, 5）は精密に。真上で水平のずれを
                # 残したまま下降すると、指の開きに余裕のない物体（りんごなど）では指が物体の上に乗る。持ち上げは粗くてよい
                tol = c.waypoint_tolerance if self.phase in (0, 1, 4, 5) else max(c.waypoint_tolerance, 0.02)
                if float(torch.linalg.norm(target - pos)) < tol:
                    self.phase += 1
        cmd.arm_goal[self.arm] = (goal, self._grasp_orientation(view, integ.quat[self.arm]))
        for g in view.cfg.grippers:
            cmd.gripper_open[g.name] = self.open if g.name == self.gripper else True
        return cmd


# --------------------------------------------------------------------------- keyboard / socket

KEY_AXES = {
    "W": (0, +1), "S": (0, -1), "A": (1, +1), "D": (1, -1), "Q": (2, +1), "E": (2, -1),
    "Z": (3, +1), "X": (3, -1), "T": (4, +1), "G": (4, -1), "C": (5, +1), "V": (5, -1),
}  # fmt: skip


class _KeyState:
    """押されているキー → 6 軸の指令。操作中の arm の切替とグリッパのトグルも管理する。"""

    def __init__(self, cfg: TeleopConfig, view: SimView):
        self.cfg = cfg
        self.arms = [a.name for a in view.cfg.arms]
        self.active = 0
        self.gripper_open = {g.name: True for g in view.cfg.grippers}
        self.flags = {"reset": False, "quit": False}

    def toggle_gripper(self, view: SimView) -> None:
        g = view.cfg.gripper_for(self.arms[self.active])
        if g:
            self.gripper_open[g.name] = not self.gripper_open[g.name]

    def command(self, axes: list[float]) -> Command:
        cmd = Command(reset=self.flags["reset"], quit=self.flags["quit"])
        self.flags["reset"] = False
        for i, arm in enumerate(self.arms):
            if i == self.active:
                cmd.arm_delta[arm] = [axes[k] * (self.cfg.pos_step if k < 3 else self.cfg.rot_step) for k in range(6)]
            else:
                cmd.arm_delta[arm] = [0.0] * 6
        cmd.gripper_open = dict(self.gripper_open)
        return cmd


class KitKeyboard(TeleopDevice):
    """Isaac Sim（Kit）のキーボード入力。キーを押している間だけ動く。"""

    name = "keyboard"

    def __init__(self, cfg: TeleopConfig, view: SimView):
        import carb
        import omni.appwindow

        self.state = _KeyState(cfg, view)
        self.view = view
        self.pressed: set[str] = set()
        self._carb = carb
        self._input = carb.input.acquire_input_interface()
        self._keyboard = omni.appwindow.get_default_app_window().get_keyboard()
        self._sub = self._input.subscribe_to_keyboard_events(self._keyboard, self._on_event)

    def _on_event(self, event, *args) -> bool:
        kinds = self._carb.input.KeyboardEventType
        # 押した / 離したイベントだけを見る（文字入力イベント CHAR では event.input が文字列で .name がない）
        if event.type not in (kinds.KEY_PRESS, kinds.KEY_RELEASE):
            return True
        name = getattr(event.input, "name", str(event.input))
        if event.type == kinds.KEY_PRESS:
            if name in KEY_AXES:
                self.pressed.add(name)
            elif name == "K":
                self.state.toggle_gripper(self.view)
            elif name == "N":
                self.state.active = (self.state.active + 1) % len(self.state.arms)
            elif name == "R":
                self.state.flags["reset"] = True
            elif name == "ESCAPE":
                self.state.flags["quit"] = True
        else:
            self.pressed.discard(name)
        return True

    def reset(self, view: SimView) -> None:
        self.pressed.clear()

    def advance(self, view: SimView, integ: TargetIntegrator) -> Command:
        axes = [0.0] * 6
        for k in self.pressed:
            i, s = KEY_AXES[k]
            axes[i] += s
        return self.state.command(axes)


class SocketKeyboard(TeleopDevice):
    """ホストの端末からキーを受け取る（`urdf2vla teleop-keys`）。端末はキーを離したことを送れないので、
    1 回のキー入力で hold_steps だけ動く（押しっぱなしならキーリピートで動き続ける）。"""

    name = "socket"

    def __init__(self, cfg: TeleopConfig, view: SimView):
        self.cfg = cfg
        self.view = view
        self.state = _KeyState(cfg, view)
        self.timers = [0] * 6
        self.signs = [0.0] * 6
        self.lock = threading.Lock()
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", cfg.socket_port))
        self.server.listen(1)
        threading.Thread(target=self._serve, daemon=True).start()
        print(f"[urdf2vla] teleop socket: 127.0.0.1:{cfg.socket_port}（別の端末で `urdf2vla teleop-keys` を実行）", flush=True)

    def _serve(self) -> None:
        while True:
            conn, _ = self.server.accept()
            with conn:
                buf = b""
                while data := conn.recv(64):
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._key(line.decode(errors="ignore").strip().upper())

    def _key(self, k: str) -> None:
        with self.lock:
            if k in KEY_AXES:
                i, s = KEY_AXES[k]
                self.timers[i], self.signs[i] = self.cfg.hold_steps, s
            elif k == "K":
                self.state.toggle_gripper(self.view)
            elif k == "N":
                self.state.active = (self.state.active + 1) % len(self.state.arms)
            elif k == "R":
                self.state.flags["reset"] = True
            elif k in ("ESC", "ESCAPE"):
                self.state.flags["quit"] = True

    def advance(self, view: SimView, integ: TargetIntegrator) -> Command:
        with self.lock:
            axes = [self.signs[i] if self.timers[i] > 0 else 0.0 for i in range(6)]
            self.timers = [max(t - 1, 0) for t in self.timers]
            return self.state.command(axes)


class MimicReplay(TeleopDevice):
    """Isaac Lab Mimic で生成したエピソード（Isaac Lab の HDF5）の行動（目標 TCP 姿勢 + グリッパ ±1）をそのまま再生する。
    エピソードの始めに生成時の初期状態（物体の位置など）を環境に書く。"""

    name = "mimic"

    def __init__(self, cfg: TeleopConfig, view: SimView, path: str):
        from isaaclab.utils.datasets import HDF5DatasetFileHandler

        self.files = HDF5DatasetFileHandler()
        self.files.open(path)
        self.names = list(self.files.get_episode_names())
        self.k = -1
        self.actions = None
        self.t = 0

    def start(self, env) -> bool:
        """次のエピソードの初期状態を環境に書く。残りがなければ False。"""
        self.k += 1
        if self.k >= len(self.names):
            return False
        ep = self.files.load_episode(self.names[self.k], env.device)
        self.actions = ep.data["actions"]
        self.t = 0
        env.reset_to(ep.data["initial_state"], torch.tensor([0], device=env.device), is_relative=True)
        return True

    def advance(self, view: SimView, integ: TargetIntegrator) -> Command:
        cmd = Command()
        if self.t >= len(self.actions):
            cmd.done = True
            a = self.actions[-1:]
        else:
            a = self.actions[self.t : self.t + 1]
            self.t += 1
        cmd.raw = a
        n_arm = len(view.cfg.arms)
        for k, g in enumerate(view.cfg.grippers):  # 行動は [腕ごとの 7 次元 ..., グリッパごとの ±1 ...]
            cmd.gripper_open[g.name] = bool(float(a[0, 7 * n_arm + k]) >= 0)
        return cmd


def make_device(kind: str, cfg: TeleopConfig, view: SimView, source: str | None = None) -> TeleopDevice:
    if kind == "mimic":
        if not source:
            raise ValueError("--device mimic には --source（Mimic で生成した HDF5）が必要です")
        return MimicReplay(cfg, view, source)
    if kind == "scripted":
        return ScriptedExpert(cfg, view)
    if kind == "keyboard":
        return KitKeyboard(cfg, view)
    if kind == "socket":
        return SocketKeyboard(cfg, view)
    raise ValueError(f"未知のデバイス: {kind}")
