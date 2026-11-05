"""urdf2vla view: ブラウザでロボット（URDF）・把持点（TCP）・台・カメラを 3D で確かめ、質問に答える画面（viser）。

Omniverse の画面を使わずに、「どの link が手先か」「指はどちらに開くか」「カメラは何を写すか」を目で見て決められるようにする。
Isaac Sim は要らない（URDF と urdf2vla の設定ファイルだけを読む）ので、inspect_robot の直後から使える。

  urdf2vla view --project projects/myrobot            # → ブラウザで http://<このマシン>:8080
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import yaml

from urdf2vla.project import Project
from urdf2vla.urdf import analysis as A
from urdf2vla.urdf.kinematics import Transform

FLOOR_Z = -0.75
QUESTION_SKILLS = ("inspect_robot", "configure_robot", "configure_task", "configure_camera", "import_robot")


def _wxyz(q_xyzw) -> tuple[float, float, float, float]:
    x, y, z, w = q_xyzw
    return (w, x, y, z)


def _mat(t: Transform) -> np.ndarray:
    import yourdfpy  # noqa: F401  (trimesh の変換関数を使う)
    from trimesh.transformations import quaternion_matrix

    m = quaternion_matrix(_wxyz(t.rot))
    m[:3, 3] = t.pos
    return m


def _pose(m: np.ndarray) -> tuple[tuple, tuple]:
    from trimesh.transformations import quaternion_from_matrix

    return tuple(float(v) for v in m[:3, 3]), tuple(float(v) for v in quaternion_from_matrix(m))  # wxyz


def _load_yaml(path: Path) -> dict | None:
    return yaml.safe_load(path.read_text()) if path.exists() else None


def view_urdf(project: Project) -> tuple[Path, object, dict, set[str]]:
    """ホスト上のメッシュの場所で書いた URDF（空の root と、SimReady に付け替えるグリッパの link を除く）。"""
    from urdf2vla.runtime.config import Runtime
    from urdf2vla.skills.robot import _load, analyze, simready_key
    from urdf2vla.urdf.normalize import write_normalized_urdf

    robot_in, model = _load(project)
    meta = analyze(project, model)
    drop: set[str] = set()
    if simready_key(robot_in) and meta.arms and meta.arms[0].gripper is not None:
        drop = set(A.subtree_links(model, meta.grippers[meta.arms[0].gripper].mount_link))
    out = project.build / "view" / "robot_view.urdf"
    write_normalized_urdf(model, out, Runtime(mode="local"), meta.meshes, drop_root=meta.root_link if meta.dummy_root else None, drop_links=drop)
    return out, meta, robot_in, drop


def pending_questions(project: Project) -> list[dict]:
    """各 Skill の最新の結果のうち、まだ回答のない質問。"""
    answered = project.answers()
    out, seen = [], set()
    for skill in QUESTION_SKILLS:
        f = project.logs / f"latest_{skill}.json"
        if not f.exists():
            continue
        for q in json.loads(f.read_text()).get("questions", []):
            if q["key"] in answered or q["key"] in seen:
                continue
            seen.add(q["key"])
            out.append({**q, "skill": skill})
    return out


def check_summary(project: Project) -> str:
    """工程の状態と検査の結果（Markdown）。"""
    lines = []
    state = json.loads((project.root / "state.json").read_text()) if (project.root / "state.json").exists() else {}
    if state:
        lines.append("**工程**")
        for skill, s in state.items():
            mark = {"ok": "✅", "needs_input": "❓", "error": "❌"}.get(s.get("status"), "・")
            lines.append(f"- {mark} {skill}")
    for name in ("import_report.yaml", "launch_report.yaml"):
        rep = _load_yaml(project.build / name)
        if rep and rep.get("checks"):
            bad = [c["name"] for c in rep["checks"] if not c["ok"]]
            lines.append(f"\n**{name.split('_')[0]} の検査**: {len(rep['checks']) - len(bad)}/{len(rep['checks'])} OK" + (f"（失敗: {', '.join(bad)}）" if bad else ""))
    return "\n".join(lines) or "まだ工程を実行していません。"


def run_viewer(project: Project, host: str = "0.0.0.0", port: int = 8080, block: bool = True):
    import viser
    import yourdfpy
    from viser.extras import ViserUrdf

    urdf_path, meta, robot_in, dropped = view_urdf(project)
    cfg = _load_yaml(project.generated / "robot_config.yaml")
    task = _load_yaml(project.generated / "task.yaml")
    cams = _load_yaml(project.generated / "cameras.yaml")

    server = viser.ViserServer(host=host, port=port, label=f"urdf2vla: {project.name}")
    server.scene.set_up_direction("+z")
    server.scene.add_grid("/grid", width=3.0, height=3.0, cell_size=0.1, position=(0.0, 0.0, FLOOR_Z if task else 0.0))

    # ---- ロボット（base pose で置く。正面が +x を向くように回したもの）
    base = Transform(tuple(cfg["base_pose"]["pos"]), tuple(cfg["base_pose"]["rot_xyzw"])) if cfg else Transform()
    base_m = _mat(base)
    pos, wxyz = _pose(base_m)
    server.scene.add_frame("/robot", show_axes=False, position=pos, wxyz=wxyz)
    # 最初の視点: ロボットと台の間を、ロボットの正面左の斜め上から見る（「Reset View」もここに戻る）
    focus = np.array(pos) if not task else (np.array(pos) + np.array([*task["table"]["center"][:2], task["table"]["top_z"]])) / 2
    server.initial_camera.look_at = tuple(float(v) for v in focus)
    server.initial_camera.position = tuple(float(v) for v in focus + np.array([1.0, 0.9, 0.7]))
    robot = ViserUrdf(server, urdf_path, root_node_name="/robot", load_collision_meshes=True, collision_mesh_color_override=(1.0, 0.3, 0.1, 0.6))
    robot.show_collision = False
    yurdf = yourdfpy.URDF.load(str(urdf_path), load_meshes=False, build_scene_graph=True)
    names = list(robot.get_actuated_joint_names())
    limits = robot.get_actuated_joint_limits()

    def link_world(link: str, q: np.ndarray) -> np.ndarray:
        yurdf.update_cfg(q)
        return base_m @ yurdf.get_transform(frame_to=link, frame_from=yurdf.base_link)

    # ---- 正面の向き（台・カメラ・teleop は +x が正面の前提）
    server.scene.add_arrows("/front", points=np.array([[[0.0, 0.0, 0.0], [0.4, 0.0, 0.0]]]), colors=(230, 60, 60), shaft_radius=0.006)
    server.scene.add_label("/front/label", "正面 (+x)", position=(0.45, 0.0, 0.0))

    # ---- 台・物体・置き場所
    if task:
        tc, ts, top = task["table"]["center"], task["table"]["size"], task["table"]["top_z"]
        h = top - FLOOR_Z
        server.scene.add_box("/scene/table", color=(184, 153, 115), dimensions=(ts[0], ts[1], h), position=(tc[0], tc[1], top - h / 2), opacity=0.5)
        from urdf2vla.schema.task import TaskConfig

        tcfg = TaskConfig.model_validate(task)  # 旧形式（object が 1 つ）も読む
        sx, sy = tcfg.spawn_x, tcfg.spawn_y
        # 物体を置く範囲（エピソードごとにこの中に 1 つ置く）と、物体の候補（外接箱。並べて表示）
        server.scene.add_box("/scene/spawn_area", color=(230, 120, 60), dimensions=(sx[1] - sx[0], sy[1] - sy[0], 0.002),
                             position=((sx[0] + sx[1]) / 2, (sy[0] + sy[1]) / 2, top + 0.001), opacity=0.35)  # fmt: skip
        palette = [(217, 51, 38), (200, 40, 40), (120, 190, 60), (190, 200, 70), (70, 120, 220), (230, 160, 40)]
        n = len(tcfg.objects)
        step = max(o.grasp_width for o in tcfg.objects) + 0.02
        for k, o in enumerate(tcfg.objects):
            x = (sx[0] + sx[1]) / 2 + (k - (n - 1) / 2) * step
            pos = (x, (sy[0] + sy[1]) / 2, top + o.size[2] / 2)
            server.scene.add_box(f"/scene/objects/{o.key}", color=palette[k % len(palette)], dimensions=tuple(o.size), position=pos, opacity=0.85)
            server.scene.add_label(f"/scene/objects/{o.key}/label", o.name, position=(0.0, 0.0, o.size[2] / 2 + 0.02))
        tg = tcfg.target
        if tg.is_container:
            server.scene.add_box("/scene/target", color=(90, 160, 120), dimensions=tuple(tg.size), position=(tg.pos[0], tg.pos[1], top + tg.size[2] / 2), opacity=0.6)
        else:
            server.scene.add_cylinder("/scene/target", radius=tg.radius, height=0.004, color=(38, 153, 64), position=(tg.pos[0], tg.pos[1], top + 0.002))
        server.scene.add_label("/scene/target/label", tg.name, position=(0.0, 0.0, max(tg.size[2], 0.01) + 0.02))

    # ---- 手先の候補と TCP
    arm_cfg = cfg["arms"][0] if cfg else None
    tcp_nodes = []
    if arm_cfg:
        tcp = server.scene.add_frame("/tcp", axes_length=0.06, axes_radius=0.003)
        appr = server.scene.add_arrows("/tcp_axes", points=np.zeros((2, 2, 3), dtype=np.float32), colors=np.array([[40, 90, 230], [230, 160, 20]], dtype=np.uint8), shaft_radius=0.003)
        server.scene.add_label("/tcp/label", "TCP（把持点）", position=(0.0, 0.0, 0.02))
        tcp_nodes = [tcp, appr]
    ee_marks = {}
    questions = pending_questions(project)
    # End Effector の質問があれば、その候補だけを表示する（候補の link が近くに集まり、名前が重なって読めないため）
    asked = {str(c) for q in questions if q["key"].endswith(".ee_link") for c in (q.get("candidates") or [])}
    for i, arm in enumerate(meta.arms):
        for cand in arm.ee_candidates:
            if cand.link in dropped or (asked and cand.link not in asked):
                continue
            f = server.scene.add_frame(f"/ee_candidates/{arm.name}/{cand.link}", axes_length=0.04, axes_radius=0.002, visible=False)
            server.scene.add_label(f"/ee_candidates/{arm.name}/{cand.link}/label", cand.link, position=(0.0, 0.0, 0.03))
            ee_marks[cand.link] = f
    gripper_proxy = None
    if cfg and cfg["grippers"] and cfg["grippers"][0].get("simready"):
        from urdf2vla.grippers import get_gripper

        sr = get_gripper(cfg["grippers"][0]["simready"])
        gripper_proxy = (server.scene.add_box("/simready_gripper", color=(40, 40, 40), dimensions=(0.03, 0.15, sr.fingertip), opacity=0.45), sr)

    # ---- カメラ（視野の錐と、launch_sim の画像）
    cam_nodes = []
    if cams:
        from PIL import Image

        for cam in cams["cameras"]:
            hfov = 2 * math.atan(cam["horizontal_aperture"] / (2 * cam["focal_length"]))
            aspect = cam["width"] / cam["height"]
            vfov = 2 * math.atan(math.tan(hfov / 2) / aspect)
            img = None
            for suffix in ("start", "end"):
                f = project.snapshots / f"{cam['name']}_{suffix}.png"
                if f.exists():
                    img = np.asarray(Image.open(f).convert("RGB"))
                    break
            node = server.scene.add_camera_frustum(f"/cameras/{cam['name']}", fov=vfov, aspect=aspect, scale=0.08, color=(20, 120, 220), image=img)
            server.scene.add_label(f"/cameras/{cam['name']}/label", cam["name"], position=(0.0, 0.0, 0.0))
            cam_nodes.append((cam, node))

    def place_dependents(q: np.ndarray) -> None:
        if arm_cfg:
            body = link_world(arm_cfg["ik_body"], q)
            t = body @ _mat(Transform(tuple(arm_cfg["tcp_offset"]["pos"]), tuple(arm_cfg["tcp_offset"]["rot_xyzw"])))
            p, w = _pose(t)
            tcp_nodes[0].position, tcp_nodes[0].wxyz = p, w
            a = body[:3, :3] @ np.array(arm_cfg["approach_axis"])
            f = body[:3, :3] @ np.array(arm_cfg["finger_axis"] or [1.0, 0.0, 0.0])
            tcp_nodes[1].points = np.array([[t[:3, 3] - 0.12 * a, t[:3, 3]], [t[:3, 3] - 0.04 * f, t[:3, 3] + 0.04 * f]], dtype=np.float32)
            if gripper_proxy:
                node, sr = gripper_proxy
                g = cfg["grippers"][0]
                mount = link_world(g["mount_link"], q) @ _mat(Transform(tuple(g["mount"]["pos"]), tuple(g["mount"]["rot_xyzw"])))
                center = mount @ np.array([0.0, 0.0, sr.fingertip / 2, 1.0])
                node.position, node.wxyz = tuple(center[:3]), _pose(mount)[1]
        for link, mark in ee_marks.items():
            mark.position, mark.wxyz = _pose(link_world(link, q))
        for cam, node in cam_nodes:
            local = _mat(Transform(tuple(cam["pos"]), tuple(cam["rot_xyzw"])))
            if cam.get("convention") == "opengl":  # OpenGL（-z 前方, +y 上）→ viser（+z 前方, -y 上）
                local = local @ np.diag([1.0, -1.0, -1.0, 1.0])
            m = link_world(cam["mount_body"], q) @ local if cam["mount"] == "link" else local
            node.position, node.wxyz = _pose(m)

    # ---- GUI: 関節・グリッパ
    default = (cfg or {}).get("default_pose") or {}

    def joint_range(n: str) -> tuple[float, float]:
        lo, hi = limits[n]
        return (-math.pi if lo is None else float(lo), math.pi if hi is None else float(hi))

    q0 = np.array([float(np.clip(default.get(n, 0.0), *joint_range(n))) for n in names])
    sliders = []
    with server.gui.add_folder("ロボット"):
        server.gui.add_markdown(f"**{meta.robot_name}**　関節 {len(names)}" + ("（グリッパは SimReady: " + cfg["grippers"][0]["simready"] + "）" if gripper_proxy else ""))
        for n, v in zip(names, q0):
            lo, hi = joint_range(n)
            sliders.append(server.gui.add_slider(n, min=lo, max=hi, step=1e-3, initial_value=float(v)))
        b_default = server.gui.add_button("初期姿勢")
        b_zero = server.gui.add_button("ゼロ姿勢")
        b_open = server.gui.add_button("グリッパを開く")
        b_close = server.gui.add_button("グリッパを閉じる")
        show_col = server.gui.add_checkbox("衝突形状を表示（オレンジ）", initial_value=False)
        show_ee = server.gui.add_checkbox("手先の候補を表示", initial_value=not bool(cfg))

    def current() -> np.ndarray:
        return np.array([s.value for s in sliders])

    def refresh(_=None) -> None:
        q = current()
        robot.update_cfg(q)
        place_dependents(q)

    for s in sliders:
        s.on_update(refresh)

    def set_values(values: dict) -> None:
        for n, s in zip(names, sliders):
            if n in values:
                s.value = float(np.clip(values[n], s.min, s.max))
        refresh()

    b_default.on_click(lambda _: set_values(default))
    b_zero.on_click(lambda _: set_values({n: 0.0 for n in names}))
    grippers = (cfg or {}).get("grippers") or []
    b_open.on_click(lambda _: set_values({k: v for g in grippers for k, v in g["open"].items()}))
    b_close.on_click(lambda _: set_values({k: v for g in grippers for k, v in g["close"].items()}))

    def on_col(_):
        robot.show_collision = show_col.value

    show_col.on_update(on_col)

    def on_ee(_):
        for m in ee_marks.values():
            m.visible = show_ee.value

    show_ee.on_update(on_ee)
    on_ee(None)

    # ---- GUI: 状態と検査
    with server.gui.add_folder("確認"):
        status_md = server.gui.add_markdown(check_summary(project))

    # ---- GUI: 質問に答える（answers.yaml に保存）
    with server.gui.add_folder("質問", expand_by_default=bool(questions)):
        if not questions:
            server.gui.add_markdown("答える質問はありません。")
        inputs = []
        for q in questions:
            server.gui.add_markdown(f"**{q['key']}**（{q['skill']}）\n\n{q['prompt']}")
            by_label = {str(c): c for c in (q.get("candidates") or [])}  # 表示は文字列、保存は元の値（数値・リストのまま）
            cands = list(by_label)
            if cands:
                w = server.gui.add_dropdown(q["key"], options=cands, initial_value=cands[0])
                if q["key"].endswith(".ee_link"):  # 選んだ候補を 3D で強調する
                    def highlight(_, w=w):
                        show_ee.value = True
                        for link, m in ee_marks.items():
                            m.axes_length = 0.12 if link == w.value else 0.04
                    w.on_update(highlight)
            else:
                w = server.gui.add_text(q["key"], initial_value=yaml.safe_dump(q.get("suggestion"), default_flow_style=True).strip() if q.get("suggestion") is not None else "")
            sug = q.get("suggestion")
            # 関節の値の提案（IK の初期姿勢など）は、答える前に 3D で見られるようにする（mimic の従動関節などスライダのない関節は無視）
            if isinstance(sug, dict) and any(k in names for k in sug):
                server.gui.add_button("提案の姿勢を見る").on_click(lambda _, sug=sug: set_values(sug))
            inputs.append((q, w, by_label))
        if questions:
            save = server.gui.add_button("回答を保存")
            saved_md = server.gui.add_markdown("")

            def on_save(_):
                # 空欄は答えていないものとして保存しない
                values = {q["key"]: (by[w.value] if by else yaml.safe_load(w.value)) for q, w, by in inputs if str(w.value).strip()}
                if not values:
                    saved_md.content = "回答が空です。"
                    return
                project.set_answers(values)
                skills = sorted({q["skill"] for q, _, _ in inputs if q["key"] in values})
                saved_md.content = f"保存しました（answers.yaml）。続けて `urdf2vla {skills[0]} --project {project.root}` を実行してください（Agent に「続けて」と頼んでもよい）。"
                status_md.content = check_summary(project)

            save.on_click(on_save)

    refresh()
    # viser は指定のポートが使われていると次の空きポートで開くので、実際のポートを表示する
    port = server.get_port()
    lan = ""
    if host == "0.0.0.0":
        import socket

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s_:
                s_.connect(("10.255.255.255", 1))  # 送信はしない（経路から自分の LAN のアドレスを知るだけ）
                lan = f"（別の PC からは http://{s_.getsockname()[0]}:{port}）"
        except OSError:
            pass
    print(f"[urdf2vla] view: http://localhost:{port} {lan} Ctrl-C で終了", flush=True)
    if not block:
        return server
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    return server
