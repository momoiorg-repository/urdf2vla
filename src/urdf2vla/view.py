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


def _tf(m: np.ndarray) -> Transform:
    """4×4 の同次変換 → Transform（四元数は (x, y, z, w)）。"""
    from trimesh.transformations import quaternion_from_matrix

    w, x, y, z = (float(v) for v in quaternion_from_matrix(m))
    return Transform(tuple(float(v) for v in m[:3, 3]), (x, y, z, w))


def node_pose(node, t: Transform) -> None:
    node.position, node.wxyz = tuple(t.pos), _wxyz(t.rot)


def _resolve_cameras(project: Project, cfg: dict | None, task: dict | None) -> tuple[list[dict], dict]:
    """configure_camera と同じ規則で、いまのカメラの指定（view で保存したもの > project.yaml > 既定）からカメラを作る（ファイルは書かない）。"""
    if not cfg or not task:
        return [], {"source": None, "error": "configure_task まで進めるとカメラを表示・編集できます。", "found": []}
    from urdf2vla.result import SkillResult
    from urdf2vla.schema.robot import RobotConfig
    from urdf2vla.skills.scene import build_cameras, camera_specs, urdf_cameras

    rc = RobotConfig.model_validate(cfg)
    found = urdf_cameras(rc)
    specs, source = camera_specs(project, rc, found)
    r = SkillResult(skill="view")
    try:
        built = build_cameras(project, rc, specs, found, r)
    except Exception as e:  # noqa: BLE001  表示用。壊れた指定でも画面は開く
        return [], {"source": source, "error": f"{type(e).__name__}: {e}", "found": found}
    return [c.model_dump(mode="json") for c in built or []], {"source": source, "error": "; ".join(r.errors) or None, "found": found}


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

    # ---- カメラ（視野の錐と、launch_sim の画像）。「カメラ」の欄で取り付け先・位置・向き・視野角を変えて保存できる
    from urdf2vla import camera_edit as CE

    arm_index = {a["ik_body"]: i for i, a in enumerate((cfg or {}).get("arms") or [])}
    cams: list[dict] = []  # 画面のカメラ: name, mount（link 名 / None = world）, local（取り付け先から見た光学 frame）, hfov, width, height, orig, touched, img, node
    cam_info = {"source": None, "error": None, "found": []}

    def snapshot(name: str):
        from PIL import Image

        for suffix in ("start", "end"):
            f = project.snapshots / f"{name}_{suffix}.png"
            if f.exists():
                return np.asarray(Image.open(f).convert("RGB"))
        return None

    def make_frustum(c: dict) -> None:
        if c.get("node") is not None:
            c["node"].remove()
        c["node"] = server.scene.add_camera_frustum(
            f"/cameras/{c['name']}", fov=CE.vfov_rad(c["hfov"], c["width"], c["height"]), aspect=c["width"] / c["height"],
            scale=0.08, color=(230, 120, 20) if c["touched"] else (20, 120, 220), image=c.get("img"),
        )  # fmt: skip
        server.scene.add_label(f"/cameras/{c['name']}/label", c["name"], position=(0.0, 0.0, 0.0))

    def load_cameras() -> None:
        for c in cams:
            if c.get("node") is not None:
                c["node"].remove()
        cams.clear()
        listed, info = _resolve_cameras(project, cfg, task)
        cam_info.update(info)
        for cam in listed:
            c = {
                "name": cam["name"], "mount": cam.get("mount_body") if cam["mount"] == "link" else None, "local": CE.optical_local(cam),
                "hfov": CE.hfov_deg(cam), "width": cam["width"], "height": cam["height"], "clip": list(cam.get("clipping_range") or [0.01, 10.0]),
                "orig": CE.original_spec(cam, arm_index),
                "touched": False, "img": snapshot(cam["name"]), "node": None,
            }  # fmt: skip
            make_frustum(c)
            cams.append(c)

    load_cameras()

    def cam_world(c: dict, q: np.ndarray) -> Transform:
        return c["local"] if c["mount"] is None else _tf(link_world(c["mount"], q)) @ c["local"]

    def meshes_near(point_world, radius: float) -> list:
        """カメラの手前 radius より近くを通るロボットのメッシュ（viser の描画は手前 1 cm から描くので、プレビューでは隠して
        シミュレータの clip_near の代わりにする。筐体・手の中にあるカメラ）。"""
        import trimesh

        handles = {h.name.rsplit("/", 1)[-1]: h for h in getattr(robot, "_meshes", [])}
        scene = getattr(getattr(robot, "_urdf", None), "scene", None)  # viser の内部。変わっていたら隠さずに描く
        if scene is None or not handles:
            return []
        p = np.linalg.inv(base_m) @ np.array([*point_world, 1.0])
        out = []
        for g, mesh in scene.geometry.items():
            h = handles.get(g)
            if h is None:
                continue
            T = scene.graph.get(g)[0]
            local = (np.linalg.inv(T) @ p)[:3]
            lo, hi = mesh.bounds
            if np.any(local < lo - radius) or np.any(local > hi + radius):
                continue
            _, dist, _ = trimesh.proximity.closest_point(mesh, local[None])
            if float(dist[0]) < radius:
                out.append(h)
        return out

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
        for c in cams:  # 光学 frame（z 前方・y 下）= viser のカメラの約束
            node_pose(c["node"], cam_world(c, q))
        place_gizmo(q)

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

    # ---- GUI: カメラ（取り付け先・位置・向き・視野角を変えて answers.yaml に保存。configure_camera が使う）
    SOURCE = {"view": "`urdf2vla view` で保存したカメラ（answers.yaml）", "project": "project.yaml の cameras", "preset": "--preset",
              "urdf": "URDF にあるカメラ（既定）", "droid": "DROID と同じ配置（既定。URDF にカメラがない）"}  # fmt: skip
    WORLD = "world（固定）"
    found_links = [l for _, c in cam_info["found"] for l in [c.link, *c.alternatives]]
    mount_options = [WORLD, *found_links, *sorted(l for l in yurdf.link_map if l not in found_links)]
    sync = {"busy": False, "views": {}}
    gizmo = server.scene.add_transform_controls("/camera_gizmo", scale=0.12, visible=False)

    def selected() -> dict | None:
        return next((c for c in cams if c["name"] == cam_sel.value), None)

    def place_gizmo(q: np.ndarray) -> None:
        c = selected() if cams else None
        gizmo.visible = c is not None and edit_on.value
        if c is not None:
            sync["busy"] = True
            node_pose(gizmo, cam_world(c, q))
            sync["busy"] = False

    with server.gui.add_folder("カメラ", expand_by_default=False):
        cam_md = server.gui.add_markdown("")
        cam_sel = server.gui.add_dropdown("カメラ", options=[c["name"] for c in cams] or ["（なし）"])
        edit_on = server.gui.add_checkbox("動かすハンドルを表示", initial_value=True)
        name_in = server.gui.add_text("名前", initial_value="")
        mount_dd = server.gui.add_dropdown("取り付け先", options=mount_options, initial_value=WORLD)
        fov_sl = server.gui.add_slider("水平の視野角 [°]", min=20, max=150, step=1, initial_value=70)
        w_in = server.gui.add_number("幅 [px]", initial_value=256, min=16, max=2048, step=1)
        h_in = server.gui.add_number("高さ [px]", initial_value=256, min=16, max=2048, step=1)
        b_prev = server.gui.add_button("プレビュー画像を作る")
        prev_img = server.gui.add_image(np.full((64, 64, 3), 235, dtype=np.uint8), label="プレビュー（ブラウザで描いた形だけの画像。実際の画像は launch_sim）")
        b_look = server.gui.add_button("このカメラから見る")
        b_back = server.gui.add_button("元の視点に戻る")
        b_add = server.gui.add_button("カメラを追加（台を見下ろす）")
        b_del = server.gui.add_button("このカメラを削除")
        b_save = server.gui.add_button("カメラを保存")
        b_clear = server.gui.add_button("保存したカメラを消す（project.yaml / 既定に戻す）")
        cam_saved = server.gui.add_markdown("")

    def cam_status() -> str:
        if cam_info["error"]:
            return cam_info["error"]
        src = SOURCE.get(cam_info["source"], "")
        changed = [c["name"] for c in cams if c["touched"]]
        return (f"いまの指定: {src}。ハンドルで動かし、「カメラを保存」で answers.yaml に書きます（オレンジの錐は未保存の変更）。"
                + (f"\n\n変更: {', '.join(changed)}" if changed else ""))  # fmt: skip

    def show_selected() -> None:
        c = selected()
        sync["busy"] = True
        if c is not None:
            name_in.value = c["name"]
            mount_dd.value = c["mount"] if c["mount"] in mount_options else WORLD
            fov_sl.value = round(c["hfov"])
            w_in.value, h_in.value = c["width"], c["height"]
            prev_img.image = c["img"] if c.get("img") is not None else np.full((64, 64, 3), 235, dtype=np.uint8)
        sync["busy"] = False
        cam_md.content = cam_status()
        place_gizmo(current())

    def refresh_names(select: str | None = None) -> None:
        sync["busy"] = True
        names_ = [c["name"] for c in cams] or ["（なし）"]
        cam_sel.options = names_
        cam_sel.value = select if select in names_ else names_[0]
        sync["busy"] = False
        show_selected()

    def touched(c: dict, redraw: bool = False) -> None:
        c["touched"] = True
        c["img"] = None  # launch_sim の画像はもう合わない
        if redraw:
            make_frustum(c)
        else:
            c["node"].color = (230, 120, 20)
            c["node"].image = None
        node_pose(c["node"], cam_world(c, current()))
        cam_md.content = cam_status()

    cam_sel.on_update(lambda _: None if sync["busy"] else show_selected())
    edit_on.on_update(lambda _: place_gizmo(current()))

    @gizmo.on_update
    def _(_):
        c = selected()
        if sync["busy"] or c is None:
            return
        world = Transform(tuple(float(v) for v in gizmo.position), (float(gizmo.wxyz[1]), float(gizmo.wxyz[2]), float(gizmo.wxyz[3]), float(gizmo.wxyz[0])))
        c["local"] = CE.relative_to(None if c["mount"] is None else _tf(link_world(c["mount"], current())), world)
        touched(c)

    @name_in.on_update
    def _(_):
        c = selected()
        new = name_in.value.strip()
        if sync["busy"] or c is None or new == c["name"]:
            return
        if not new or any(o["name"] == new for o in cams):
            cam_saved.content = f"名前 '{new}' は使えません（空・重複）。"
            return
        c["name"] = new
        touched(c, redraw=True)
        refresh_names(new)

    @mount_dd.on_update
    def _(_):
        c = selected()
        if sync["busy"] or c is None:
            return
        world = cam_world(c, current())  # 見た目の位置は変えずに、取り付け先から見た姿勢を付け替える
        c["mount"] = None if mount_dd.value == WORLD else mount_dd.value
        c["local"] = CE.relative_to(None if c["mount"] is None else _tf(link_world(c["mount"], current())), world)
        touched(c)

    def on_optics(_):
        c = selected()
        if sync["busy"] or c is None:
            return
        c["hfov"], c["width"], c["height"] = float(fov_sl.value), int(w_in.value), int(h_in.value)
        touched(c, redraw=True)

    for w_ in (fov_sl, w_in, h_in):
        w_.on_update(on_optics)

    @b_prev.on_click
    def _(event):
        c = selected()
        if c is None or event.client is None:
            return
        # viser の描画（get_render）は手前 1 cm から描くので、筐体や手の中にあるカメラ（clip_near が大きい）では、
        # カメラの手前 clip_near より近くを通るロボットのメッシュを隠して描く（シミュレータの clip_near の代わり）
        t = cam_world(c, current())
        hidden = [c_["node"] for c_ in cams] + [gizmo, *tcp_nodes]
        if c["clip"][0] > 0.015:
            hidden += meshes_near(t.pos, c["clip"][0])
        vis = [n.visible for n in hidden]
        for n in hidden:
            n.visible = False
        try:
            img = event.client.get_render(height=int(c["height"]), width=int(c["width"]), wxyz=_wxyz(t.rot), position=tuple(t.pos),
                                          fov=CE.vfov_rad(c["hfov"], c["width"], c["height"]), transport_format="jpeg")  # fmt: skip
        finally:
            for n, v in zip(hidden, vis):
                n.visible = v
        rgb = np.ascontiguousarray(img[..., :3])  # JPEG は背景つきの RGB（PNG は背景が透明の RGBA で、RGB だけ取ると黒くなる）
        prev_img.image = rgb
        c["node"].image = rgb
        # Agent（ブラウザを見られない）も確かめられるように、ファイルにも書く
        from PIL import Image

        out = project.build / "view" / f"preview_{c['name']}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb).save(out)
        cam_saved.content = f"プレビューを {project.rel(out)} にも保存しました。"

    @b_look.on_click
    def _(event):
        c = selected()
        if c is None or event.client is None:
            return
        cam = event.client.camera
        sync["views"].setdefault(event.client.client_id, (cam.position.copy(), cam.wxyz.copy(), cam.fov, cam.near))
        t = cam_world(c, current())
        c["node"].visible = False  # 自分の錐（画像の板）と動かすハンドルが視界をふさがないように
        gizmo.visible = False
        with event.client.atomic():
            cam.wxyz = _wxyz(t.rot)
            cam.position = tuple(t.pos)
            cam.fov = CE.vfov_rad(c["hfov"], c["width"], c["height"])
        cam.near = c["clip"][0]
        event.client.flush()

    @b_back.on_click
    def _(event):
        if event.client is None or event.client.client_id not in sync["views"]:
            return
        pos, wxyz, fov, near = sync["views"].pop(event.client.client_id)
        with event.client.atomic():
            event.client.camera.wxyz, event.client.camera.position, event.client.camera.fov = wxyz, pos, fov
        event.client.camera.near = near
        event.client.flush()
        for c in cams:
            c["node"].visible = True
        place_gizmo(current())

    @b_add.on_click
    def _(_):
        if not task:
            cam_saved.content = "configure_task まで進めてから追加してください（台の位置を使います）。"
            return
        k = 1
        while any(c["name"] == f"cam_{k}" for c in cams):
            k += 1
        span = max(task["table"]["size"][0], 0.6)
        c = {"name": f"cam_{k}", "mount": None, "local": CE.new_world_camera(tuple(task["table"]["center"][:2]), task["table"]["top_z"], span),
             "hfov": 70.0, "width": 256, "height": 256, "clip": [0.01, 10.0], "orig": None, "touched": True, "img": None, "node": None}  # fmt: skip
        make_frustum(c)
        cams.append(c)
        node_pose(c["node"], cam_world(c, current()))
        refresh_names(c["name"])

    @b_del.on_click
    def _(_):
        c = selected()
        if c is None:
            return
        c["node"].remove()
        cams.remove(c)
        cam_info["source"] = cam_info["source"] or "view"
        cam_saved.content = f"{c['name']} を削除しました（保存するまで answers.yaml は変わりません）。"
        refresh_names()

    @b_save.on_click
    def _(_):
        if not cams:
            cam_saved.content = "カメラが 1 台もありません。"
            return
        specs = [CE.explicit_spec(c["name"], c["mount"], c["local"], c["hfov"], c["width"], c["height"], clipping_range=c["clip"]) if c["touched"] else c["orig"]
                 for c in cams]  # fmt: skip
        project.set_answers({"cameras": specs})
        for c in cams:
            c["orig"] = specs[cams.index(c)]
            c["touched"] = False
            make_frustum(c)
            node_pose(c["node"], cam_world(c, current()))
        cam_info["source"] = "view"
        body = yaml.safe_dump({"cameras": specs}, allow_unicode=True, sort_keys=False, default_flow_style=None)
        cam_saved.content = (f"保存しました（answers.yaml の cameras。project.yaml の cameras より優先）。続けて "
                             f"`urdf2vla configure_camera --project {project.root}` と `launch_sim` で実際の画像を確かめてください。"
                             f"project.yaml に移すなら:\n\n```yaml\n{body}```")  # fmt: skip
        status_md.content = check_summary(project)
        show_selected()

    @b_clear.on_click
    def _(_):
        project.clear_answers(["cameras"])
        load_cameras()
        cam_saved.content = "answers.yaml の cameras を消しました。project.yaml の cameras（なければ既定）に戻ります。"
        refresh_names()

    refresh_names()

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
